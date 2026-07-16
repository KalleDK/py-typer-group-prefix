"""typer-group-prefix – add CLI and environment-variable prefixes to Typer dependency groups.

This package solves a common problem when building complex Typer applications with
reusable dependency-injection parsers: option name collisions.  When you want to use
the same parser (e.g. a database config) for two different databases in the same
command, every ``--host`` / ``HOST`` option would clash.

``typer-group-prefix`` lets you attach a *prefix* to every option inside a parser so
that, for example, ``--host`` becomes ``--primary-host`` and ``--replica-host``, while
the environment variables become ``PRIMARY_HOST`` and ``REPLICA_HOST``.

Typical usage::

    from typer_group_prefix import TyperGroup, typer_group
    from typer_di import Depends, TyperDI
    import typer

    def parse_db(
        host: Annotated[str, typer.Option("--host", envvar="HOST")] = "localhost",
        port: Annotated[int, typer.Option("--port", envvar="PORT")] = 5432,
    ) -> dict:
        return {"host": host, "port": port}

    DB = TyperGroup(panel="Database", parser=parse_db)

    app = TyperDI()

    @app.command()
    def run(
        primary: dict = DB.with_options(prefix="primary")(),
        replica: dict = DB.with_options(prefix="replica")(),
    ) -> None:
        ...

See :class:`TyperGroup` and :func:`typer_group` for the main API surface.
See :class:`TyperGroupFromDataclass` for automatic dataclass-to-CLI conversion.
"""

import copy
import dataclasses
import enum
import inspect
from collections.abc import Callable, Sequence
from functools import wraps
from typing import (
    Annotated,
    Any,
    Generic,
    NamedTuple,
    Self,
    TypeVar,
    cast,
)

import typer
import typer_di

from typer_group_prefix.parsers import DEFAULT_PARSERS

__version__ = "0.1.12"


T = TypeVar("T")


class Replacements(NamedTuple):
    """Internal record that captures the original names/defaults of an option before transformation."""

    name: str
    default: str
    extra: tuple[str, ...]
    env: str | None


class NotSet(enum.Enum):
    """Sentinel enum used to distinguish *"not provided"* from an explicit ``None``.

    Because many parameters accept ``None`` as a meaningful value (meaning
    "remove the prefix"), a separate sentinel is needed so callers can omit a
    parameter without accidentally overriding an existing value.
    """

    NOTSET = enum.auto()


NOTSET = NotSet.NOTSET
"""The singleton sentinel value – use this instead of instantiating :class:`NotSet`."""


def get_args(info: typer.models.OptionInfo) -> tuple[str, ...]:
    """Return all argument names stored on *info* as a flat tuple.

    Typer stores the *primary* option name in ``OptionInfo.default`` (when the
    first positional argument to :func:`typer.Option` starts with ``--``) and
    any *additional* names in ``OptionInfo.param_decls``.  This helper reunites
    them into a single sequence in declaration order.

    Args:
        info: A :class:`typer.models.OptionInfo` whose ``default`` field holds
              the primary option name string (e.g. ``"--host"``).

    Returns:
        A tuple of all option name strings, primary first.

    Raises:
        ValueError: If ``info.default`` is not a string.
    """
    if not isinstance(info.default, str):
        raise ValueError("Only str allowed for args")
    return tuple([info.default, *(info.param_decls or ())])


def set_args(info: typer.models.OptionInfo, values: Sequence[str]) -> None:
    """Write a new set of argument names back onto *info*.

    This is the counterpart to :func:`get_args`.  The first element of *values*
    becomes ``info.default`` and the remainder become ``info.param_decls``.

    Args:
        info:   The :class:`~typer.models.OptionInfo` to mutate.
        values: New option names; must contain at least one element.

    Raises:
        ValueError: If *values* is empty.
    """
    if not values:
        raise ValueError("No values provided")
    info.default = values[0]
    info.param_decls = values[1:]


@dataclasses.dataclass(slots=True, kw_only=True)
class TyperPrefix:
    """Low-level transformer that rewrites option names and envvar names on a function signature.

    :class:`TyperPrefix` is the engine behind :class:`TyperGroup`.  It operates
    directly on :class:`typer.models.OptionInfo` objects, applying a CLI prefix
    (e.g. ``"primary"`` → ``--primary-host``) and an environment-variable prefix
    (e.g. ``"PRIMARY"`` → ``PRIMARY_HOST``).

    Attributes:
        cli_prefix:  Prefix to prepend to ``--long`` options, or ``None`` to leave names
                     unchanged.  Normalised to lowercase with hyphens on construction.
        env_prefix:  Prefix to prepend to environment variable names, or ``None`` to leave
                     them unchanged.  Normalised to uppercase with underscores on construction.
        panel:       The Rich help-panel heading to assign to every option managed by this
                     instance.
        keep_short:  When ``True``, short single-dash flags (e.g. ``-v``) are included in the
                     transformed argument list (converted to ``--prefix-v``).  When ``False``
                     (default) short flags are silently dropped.
        keep_panels: When ``True``, options that already have a ``rich_help_panel`` set keep
                     their original panel instead of being moved to *panel*.
    """

    cli_prefix: str | None
    env_prefix: str | None
    panel: str
    keep_short: bool = False
    keep_panels: bool = False

    def __post_init__(self) -> None:
        # Normalise cli_prefix: lowercase, hyphens instead of underscores, strip leading/trailing hyphens.
        if self.cli_prefix is not None:
            self.cli_prefix = self.cli_prefix.replace("_", "-").strip("-").strip().lower()
            if self.cli_prefix == "":
                self.cli_prefix = None

        # Normalise env_prefix: uppercase, underscores instead of hyphens, strip leading/trailing underscores.
        if self.env_prefix is not None:
            self.env_prefix = self.env_prefix.replace("-", "_").strip("_").strip().upper()
            if self.env_prefix == "":
                self.env_prefix = None

    def make_env(self, info: typer.models.OptionInfo) -> str | list[str] | None:
        """Build the new envvar name(s) for *info* by applying :attr:`env_prefix`.

        Args:
            info: The option whose ``envvar`` field will be prefixed.

        Returns:
            * ``None`` if the option has no envvar.
            * A plain string if the option has a single envvar.
            * A list of strings if the option has multiple envvars.
        """
        value = info.envvar

        if value is None:
            return None

        if self.env_prefix is None:
            if isinstance(value, str):
                return value.upper()
            return [v.upper() for v in value]

        if isinstance(value, str):
            return f"{self.env_prefix}_{value.upper()}"
        return [f"{self.env_prefix}_{v.upper()}" for v in value]

    def make_args(self, info: typer.models.OptionInfo) -> tuple[str, ...]:
        """Build the new argument name(s) for *info* by applying :attr:`cli_prefix`.

        Short flags (single-dash, e.g. ``-v``) are included only when
        :attr:`keep_short` is ``True``, in which case they are converted to
        long-style prefixed names (e.g. ``--prefix-v``).

        Args:
            info: The option whose argument names will be prefixed.

        Returns:
            A non-empty tuple of option name strings.

        Raises:
            ValueError: If no argument names remain after filtering.
        """
        if self.cli_prefix is None:
            return tuple(arg for arg in get_args(info) if arg.startswith("--") or self.keep_short)

        _args = tuple(
            f"--{self.cli_prefix}-{arg.strip('-')}" for arg in get_args(info) if arg.startswith("--") or self.keep_short
        )

        if not _args:
            raise ValueError("No args generated")

        return _args

    def update_info(self, info: typer.models.OptionInfo) -> str:
        """Apply this prefix to *info* in-place and return the new Python parameter name.

        Updates the option's envvar, help panel, and CLI argument names, then returns
        the normalised Python identifier that should be used as the parameter name in
        the wrapped function's signature (lowercase, underscores, no leading/trailing
        underscores).

        Args:
            info: The :class:`~typer.models.OptionInfo` to mutate.

        Returns:
            The new Python-safe parameter name derived from the last transformed argument.
        """
        info.envvar = self.make_env(info)
        if info.rich_help_panel is None or self.keep_panels is False:
            info.rich_help_panel = self.panel
        _args = self.make_args(info)
        set_args(info, _args)
        return _args[-1].replace("-", "_").lower().strip("_")

    def create_signature(self, fn: Callable[..., Any]) -> tuple[inspect.Signature, dict[str, str]]:
        """Create a transformed :class:`inspect.Signature` for *fn* with prefixed parameter names.

        Each parameter in *fn* is processed:

        * If it has a :class:`typer_di.DependsType` default, its inner callback is
          recursively wrapped via :meth:`wrap_parser`.
        * Otherwise its :class:`~typer.models.OptionInfo` annotation metadata is
          updated via :meth:`update_info` and the parameter is renamed to match the
          new CLI argument name.

        Args:
            fn: The parser function whose signature should be transformed.

        Returns:
            A 2-tuple of ``(new_signature, kwargs_names)`` where *kwargs_names* maps
            new parameter names back to original parameter names (needed by the
            wrapper closure to forward keyword arguments correctly).

        Raises:
            ValueError: If a non-Depends parameter has no :class:`~typer.models.OptionInfo`
                        in its annotation metadata.
        """
        signature = copy.deepcopy(inspect.signature(fn))
        kwargs_names: dict[str, str] = {}

        parameters: list[inspect.Parameter] = []

        for parameter in signature.parameters.values():
            if isinstance(parameter.default, typer_di.DependsType):
                wrapped_parser = self.wrap_parser(parameter.default.callback)
                parameter = parameter.replace(default=wrapped_parser)
                new_name = parameter.name
            else:
                for info in parameter.annotation.__metadata__:
                    if isinstance(info, typer.models.OptionInfo):
                        new_name = self.update_info(info)
                        break
                else:
                    raise ValueError(f"No Option found for {parameter.name} in {fn}")

            kwargs_names[new_name] = parameter.name
            parameter = parameter.replace(name=new_name)
            parameters.append(parameter)

        return signature.replace(parameters=parameters), kwargs_names

    def wrap_parser(self, parser: Callable[..., T]) -> T:
        """Wrap *parser* so that its CLI options are prefixed and its return type is preserved.

        Creates a new callable with a transformed signature (via :meth:`create_signature`)
        and registers it as a :func:`typer_di.Depends` dependency.  The wrapper re-maps
        the renamed kwargs back to the original names before calling *parser*.

        Args:
            parser: The parser function to wrap.

        Returns:
            A :func:`typer_di.Depends` instance whose callback is the prefixed wrapper.
        """
        signature, kwargs_new_names = self.create_signature(parser)

        @wraps(parser)
        def _wrapper(*args: Any, **kwargs: Any) -> T:
            kwargs = {kwargs_new_names[kw]: val for kw, val in kwargs.items()}
            return parser(*args, **kwargs)

        setattr(_wrapper, "__signature__", signature)
        delattr(_wrapper, "__wrapped__")
        return typer_di.Depends(_wrapper)

    @classmethod
    def from_prefix(
        cls,
        default_panel: str,
        default_prefix: str | None = None,
        default_cli_prefix: str | None = None,
        default_env_prefix: str | None = None,
        panel: str | NotSet = NOTSET,
        prefix: str | None | NotSet = NOTSET,
        cli_prefix: str | None | NotSet = NOTSET,
        env_prefix: str | None | NotSet = NOTSET,
        keep_short: bool = False,
    ) -> Self:
        """Construct a :class:`TyperPrefix` from layered default and override values.

        The prefix resolution order (highest priority first):

        1. Explicit ``cli_prefix`` / ``env_prefix`` override.
        2. Generic ``prefix`` override (applies to both CLI and env).
        3. Specific ``default_cli_prefix`` / ``default_env_prefix`` default.
        4. Generic ``default_prefix`` default.

        Args:
            default_panel:       Fallback panel heading when *panel* is not set.
            default_prefix:      Fallback prefix for both CLI and env when no more
                                 specific default is provided.
            default_cli_prefix:  Fallback CLI-only prefix.
            default_env_prefix:  Fallback env-only prefix.
            panel:               Override for the panel heading.
            prefix:              Override for both CLI and env prefixes.
            cli_prefix:          Override for the CLI prefix only.
            env_prefix:          Override for the env prefix only.
            keep_short:          Passed directly to the constructed instance.

        Returns:
            A new :class:`TyperPrefix` instance.
        """
        _panel = panel if panel is not NOTSET else default_panel

        def clean_cli_prefix(value: str | None) -> str | None:
            if value is None:
                return None
            value = value.replace("_", "-").strip("-").strip()
            if value == "":
                return None
            return value.lower()

        def clean_env_prefix(value: str | None) -> str | None:
            if value is None:
                return None
            value = value.replace("-", "_").strip("_").strip()
            if value == "":
                return None
            return value.upper()

        def find_prefix(
            specific: str | None | NotSet,
            general: str | None | NotSet,
            default_specific: str | None,
            default_general: str | None,
        ) -> str | None:
            if specific is not NOTSET:
                return specific
            if general is not NOTSET:
                return general
            if default_specific is not None:
                return default_specific
            return default_general

        _cli_prefix = clean_cli_prefix(find_prefix(cli_prefix, prefix, default_cli_prefix, default_prefix))
        _env_prefix = clean_env_prefix(find_prefix(env_prefix, prefix, default_env_prefix, default_prefix))

        return cls(
            cli_prefix=_cli_prefix,
            env_prefix=_env_prefix,
            panel=_panel,
            keep_short=keep_short,
        )


def _calc_panel(current: str, new: str | NotSet, prepend: bool) -> str:
    if new is NOTSET:
        return current
    if not prepend:
        return new
    return f"{new} {current}"


def _calc_prefix(current: str | None, new: str | None | NotSet, prepend: bool) -> str | None:
    if new is NOTSET:
        return current
    if not prepend:
        return new
    if current is None:
        return new
    if new is None:
        return current
    return f"{new}-{current}"


def _calc_specific_prefix(
    current: str | None | NotSet,
    new: str | None | NotSet,
    new_default: str | None | NotSet,
    prepend: bool,
    prepend_default: bool,
) -> str | None | NotSet:
    if new is NOTSET:
        if current is NOTSET:
            return NOTSET
        if new_default is NOTSET:
            return current
        if not prepend_default or current is None:
            return new_default
        if new_default is None:
            return current
        return f"{new_default}-{current}"

    if not prepend or current is NOTSET or current is None:
        return new
    if new is None:
        return current
    return f"{new}-{current}"


@dataclasses.dataclass(slots=True, kw_only=True)
class TyperGroup(Generic[T]):
    """A reusable, composable container that wraps a Typer parser function with prefix support.

    :class:`TyperGroup` is the primary user-facing class in this library.  Create one
    instance per "logical group" of CLI options, then call :meth:`with_options` to
    derive namespace-specific variants (e.g. ``--primary-host`` vs ``--replica-host``).

    Attributes:
        panel:       Rich help-panel heading shown in ``--help`` output.
        prefix:      Default prefix for both CLI args and env vars when neither
                     :attr:`cli_prefix` nor :attr:`env_prefix` is explicitly set.
        env_prefix:  Specific env-var prefix.  :data:`NOTSET` means fall back to
                     :attr:`prefix`.
        cli_prefix:  Specific CLI-arg prefix.  :data:`NOTSET` means fall back to
                     :attr:`prefix`.
        keep_short:  When ``True``, single-dash flags are included in the transformed
                     argument list (converted to long-style prefixed names).
        keep_panels: When ``True``, options that already specify a ``rich_help_panel``
                     retain their original panel assignment.
        parser:      The parser function whose parameters will be prefixed.

    Example::

        DB = TyperGroup(panel="Database", parser=parse_db)
        primary_db = DB.with_options(prefix="primary")()   # Depends(...)
        replica_db = DB.with_options(prefix="replica")()   # Depends(...)
    """

    panel: str
    prefix: str | None = None
    env_prefix: str | None | NotSet = NOTSET
    cli_prefix: str | None | NotSet = NOTSET
    keep_short: bool = False
    keep_panels: bool = False
    parser: Callable[..., T]

    def __call__(self) -> T:
        """Shorthand for :meth:`build`; returns the wrapped :func:`typer_di.Depends` dependency."""
        return self.build()

    def with_options(
        self,
        *,
        panel: str | NotSet = NOTSET,
        prefix: str | None | NotSet = NOTSET,
        cli_prefix: str | None | NotSet = NOTSET,
        env_prefix: str | None | NotSet = NOTSET,
        prepend_panel: bool = False,
        prepend_prefix: bool = False,
        prepend_env_prefix: bool = False,
        prepend_cli_prefix: bool = False,
        keep_short: bool | NotSet = NOTSET,
        keep_panels: bool | NotSet = NOTSET,
    ) -> "TyperGroup[T]":
        """Return a new :class:`TyperGroup` derived from this one with overridden options.

        Unspecified parameters inherit their values from the current instance.
        The ``prepend_*`` flags control whether the new value is prepended to (rather
        than replacing) the current value, allowing hierarchical prefix composition.

        Args:
            panel:              New panel heading, or :data:`NOTSET` to keep current.
            prefix:             New prefix for both CLI and env, or :data:`NOTSET` to keep current.
            cli_prefix:         New CLI-only prefix override.
            env_prefix:         New env-only prefix override.
            prepend_panel:      If ``True``, prepend *panel* to the current panel string.
            prepend_prefix:     If ``True``, prepend *prefix* to the current prefix.
            prepend_env_prefix: If ``True``, prepend *env_prefix* to the current env prefix.
            prepend_cli_prefix: If ``True``, prepend *cli_prefix* to the current CLI prefix.
            keep_short:         Override :attr:`keep_short`, or :data:`NOTSET` to inherit.
            keep_panels:        Override :attr:`keep_panels`, or :data:`NOTSET` to inherit.

        Returns:
            A new :class:`TyperGroup` instance; the original is not modified.
        """
        return TyperGroup(
            panel=_calc_panel(self.panel, panel, prepend_panel),
            prefix=_calc_prefix(self.prefix, prefix, prepend_prefix),
            env_prefix=_calc_specific_prefix(self.env_prefix, env_prefix, prefix, prepend_env_prefix, prepend_prefix),
            cli_prefix=_calc_specific_prefix(self.cli_prefix, cli_prefix, prefix, prepend_cli_prefix, prepend_prefix),
            parser=self.parser,
            keep_short=self.keep_short if keep_short is NOTSET else keep_short,
            keep_panels=self.keep_panels if keep_panels is NOTSET else keep_panels,
        )

    def build(self) -> T:
        """Build and return the :func:`typer_di.Depends` dependency for this group.

        Constructs a :class:`TyperPrefix` from the current settings and wraps
        :attr:`parser` via :meth:`TyperPrefix.wrap_parser`.

        Returns:
            A :func:`typer_di.Depends` instance that Typer-DI will resolve by calling
            the prefixed parser.
        """
        tp = TyperPrefix(
            env_prefix=self.env_prefix if self.env_prefix is not NOTSET else self.prefix,
            cli_prefix=self.cli_prefix if self.cli_prefix is not NOTSET else self.prefix,
            panel=self.panel,
            keep_short=self.keep_short,
            keep_panels=self.keep_panels,
        )
        return tp.wrap_parser(self.parser)


def typer_group(
    panel: str,
    prefix: str | None = None,
    env_prefix: str | None | NotSet = NOTSET,
    cli_prefix: str | None | NotSet = NOTSET,
    keep_short: bool = False,
    keep_panels: bool = False,
) -> Callable[[Callable[..., T]], TyperGroup[T]]:
    """Decorator that wraps a parser function in a :class:`TyperGroup`.

    This is a convenience alternative to constructing a :class:`TyperGroup` directly::

        @typer_group(panel="Database", prefix="db")
        def parse_db(
            host: Annotated[str, typer.Option("--host", envvar="HOST")] = "localhost",
        ) -> dict:
            return {"host": host}

        # Equivalent to:
        # DB = TyperGroup(panel="Database", prefix="db", parser=parse_db)

    Args:
        panel:       Rich help-panel heading.
        prefix:      Default prefix for both CLI args and env vars.
        env_prefix:  Specific env-var prefix (overrides *prefix* for env vars).
        cli_prefix:  Specific CLI-arg prefix (overrides *prefix* for CLI args).
        keep_short:  Include short flags in the transformed argument list.
        keep_panels: Preserve existing ``rich_help_panel`` assignments.

    Returns:
        A decorator that converts the decorated function into a :class:`TyperGroup`.
    """

    def _wrap(c: Callable[..., T]) -> TyperGroup[T]:
        return TyperGroup(
            parser=c,
            panel=panel,
            prefix=prefix,
            env_prefix=env_prefix,
            cli_prefix=cli_prefix,
            keep_short=keep_short,
            keep_panels=keep_panels,
        )

    return _wrap


@dataclasses.dataclass(slots=True, kw_only=True)
class _TyperDataGroup(Generic[T]):
    """Internal builder that synthesises a :class:`TyperGroup` from a dataclass definition.

    This class is not part of the public API; use :func:`TyperGroupFromDataclass` instead.
    It inspects the dataclass constructor signature and generates :class:`~typer.models.OptionInfo`
    annotations for each field, then constructs a :class:`TyperGroup` around the resulting
    synthetic parser.
    """

    panel: str
    prefix: str | None = None
    env_prefix: str | None | NotSet = NOTSET
    cli_prefix: str | None | NotSet = NOTSET
    keep_short: bool = False
    keep_panels: bool = False
    clss: type[T]
    parsers: dict[type, tuple[str, Callable[[str], Any]]] = dataclasses.field(
        default_factory=dict[type, tuple[str, Callable[[str], Any]]]
    )

    def build(self) -> TyperGroup[T]:
        """Build and return the :class:`TyperGroup` for this dataclass."""

        @self._wrapper
        def _wrapped(**kwargs: Any) -> T:
            return self.clss(**kwargs)

        return TyperGroup(
            panel=self.panel,
            prefix=self.prefix,
            env_prefix=self.env_prefix,
            cli_prefix=self.cli_prefix,
            keep_short=self.keep_short,
            keep_panels=self.keep_panels,
            parser=_wrapped,
        )

    def __call__(self) -> TyperGroup[T]:
        """Shorthand for :meth:`build`."""
        return self.build()

    def _get_parser(self, f: type[Any]) -> None | Callable[..., Any]:
        """Look up the string parser for field type *f* from custom and default registries."""
        _parser = self.parsers.get(f, DEFAULT_PARSERS.get(f))
        if _parser is None:
            return None

        def _wrap(v: str) -> Any:
            return _parser[1](v)

        _wrap.__name__ = _parser[0]
        return _wrap

    def make_anno(self, name: str, f: type[Any]) -> Any:
        """Build an ``Annotated[f, typer.Option(...)]`` annotation for dataclass field *name*.

        Automatically configures ``prompt=True`` and ``hide_input=True`` for
        ``SecretStr`` fields and attaches a string parser when one is registered for *f*.

        Args:
            name: The dataclass field name (used to derive the CLI option name and envvar).
            f:    The field's type annotation (unwrapped from :class:`~dataclasses.InitVar`
                  if necessary).

        Returns:
            An ``Annotated`` type alias with a fully configured :class:`~typer.models.OptionInfo`.
        """
        if isinstance(f, dataclasses.InitVar):
            f = cast(type[Any], f.type)  # pyright: ignore[reportUnknownMemberType]

        parser = self._get_parser(f)

        if hasattr(f, "__name__") and f.__name__ == "SecretStr":
            return Annotated[
                f,
                typer.Option(
                    f"--{name.replace('_', '-')}",
                    envvar=name.upper(),
                    prompt=True,
                    hide_input=True,
                    parser=parser,
                ),
            ]
        return Annotated[
            f,
            typer.Option(f"--{name.replace('_', '-')}", envvar=name.upper(), show_default=True, parser=parser),
        ]

    def _wrapper(self, func: Callable[..., T]) -> Callable[..., T]:
        """Inject a synthesised ``__signature__`` onto *func* based on the dataclass fields."""
        sig = inspect.signature(self.clss)

        signature = inspect.Signature(
            [f.replace(annotation=self.make_anno(name, f.annotation)) for name, f in sig.parameters.items()],
            return_annotation=self.clss,
        )

        setattr(func, "__signature__", signature)
        return func


def TyperGroupFromDataclass(
    clss: type[T],
    panel: str,
    prefix: str | None = None,
    env_prefix: str | None | NotSet = NOTSET,
    cli_prefix: str | None | NotSet = NOTSET,
    keep_short: bool = False,
    keep_panels: bool = False,
    parsers: dict[type, tuple[str, Callable[[str], Any]]] | None = None,
) -> TyperGroup[T]:
    """Create a :class:`TyperGroup` from a dataclass, automatically deriving CLI options.

    Each field in *clss* becomes a ``--field-name`` option with ``FIELD_NAME`` as the
    default environment variable.  The options can then be prefixed via
    :meth:`TyperGroup.with_options` just like any other group.

    Special handling:

    * ``SecretStr`` fields get ``prompt=True`` and ``hide_input=True``.
    * :class:`~datetime.timedelta` fields (and any type registered in *parsers* or
      :data:`~typer_group_prefix.parsers.DEFAULT_PARSERS`) use the registered string parser.
    * :class:`~dataclasses.InitVar` fields are included with their unwrapped type.

    Args:
        clss:        The dataclass type to convert.
        panel:       Rich help-panel heading.
        prefix:      Default prefix for both CLI args and env vars.
        env_prefix:  Specific env-var prefix (overrides *prefix* for env vars).
        cli_prefix:  Specific CLI-arg prefix (overrides *prefix* for CLI args).
        keep_short:  Include short flags in the transformed argument list.
        keep_panels: Preserve existing ``rich_help_panel`` assignments.
        parsers:     Additional ``{type: (metavar, parser_fn)}`` entries to merge with
                     :data:`~typer_group_prefix.parsers.DEFAULT_PARSERS`.

    Returns:
        A :class:`TyperGroup` whose parser instantiates *clss* from CLI/env arguments.

    Example::

        @dataclasses.dataclass
        class DBConfig:
            host: str
            port: int = 5432

        DB = TyperGroupFromDataclass(DBConfig, panel="Database", prefix="db")
        # Generates --db-host / DB_HOST and --db-port / DB_PORT options.
    """
    return _TyperDataGroup(
        panel=panel,
        prefix=prefix,
        env_prefix=env_prefix,
        cli_prefix=cli_prefix,
        keep_short=keep_short,
        keep_panels=keep_panels,
        clss=clss,
        parsers=parsers or {},
    ).build()
