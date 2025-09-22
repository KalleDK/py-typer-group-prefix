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

__version__ = "0.1.10"


T = TypeVar("T")


class Replacements(NamedTuple):
    name: str
    default: str
    extra: tuple[str, ...]
    env: str | None


class NotSet(enum.Enum):
    NOTSET = enum.auto()


NOTSET = NotSet.NOTSET


def get_args(info: typer.models.OptionInfo):
    if not isinstance(info.default, str):
        raise ValueError("Only str allowed for args")
    return tuple([info.default, *(info.param_decls or ())])


def set_args(info: typer.models.OptionInfo, values: Sequence[str]):
    if not values:
        raise ValueError("No values provided")
    info.default = values[0]
    info.param_decls = values[1:]


@dataclasses.dataclass(slots=True, kw_only=True)
class TyperPrefix:
    cli_prefix: str | None
    env_prefix: str | None
    panel: str
    keep_short: bool = False
    keep_panels: bool = False

    def __post_init__(self):
        if self.cli_prefix is not None:
            self.cli_prefix = self.cli_prefix.replace("_", "-").strip("-").strip().lower()
            if self.cli_prefix == "":
                self.cli_prefix = None

        if self.env_prefix is not None:
            self.env_prefix = self.env_prefix.replace("-", "_").strip("_").strip().upper()
            if self.env_prefix == "":
                self.env_prefix = None

    def make_env(self, info: typer.models.OptionInfo):
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

    def make_args(self, info: typer.models.OptionInfo):
        if self.cli_prefix is None:
            return tuple(arg for arg in get_args(info) if arg.startswith("--") or self.keep_short)

        _args = tuple(
            f"--{self.cli_prefix}-{arg.strip('-')}" for arg in get_args(info) if arg.startswith("--") or self.keep_short
        )

        if not _args:
            raise ValueError("No args generated")

        return _args

    def update_info(self, info: typer.models.OptionInfo):
        info.envvar = self.make_env(info)
        if info.rich_help_panel is None or self.keep_panels is False:
            info.rich_help_panel = self.panel
        _args = self.make_args(info)
        set_args(info, _args)
        return _args[-1].replace("-", "_").lower().strip("_")

    def create_signature(self, fn: Callable[..., Any]) -> tuple[inspect.Signature, dict[str, str]]:
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
    panel: str
    prefix: str | None = None
    env_prefix: str | None | NotSet = NOTSET
    cli_prefix: str | None | NotSet = NOTSET
    keep_short: bool = False
    keep_panels: bool = False
    parser: Callable[..., T]

    def __call__(self) -> T:
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
    ) -> "TyperGroup[T]":
        return TyperGroup(
            panel=_calc_panel(self.panel, panel, prepend_panel),
            prefix=_calc_prefix(self.prefix, prefix, prepend_prefix),
            env_prefix=_calc_specific_prefix(self.env_prefix, env_prefix, prefix, prepend_env_prefix, prepend_prefix),
            cli_prefix=_calc_specific_prefix(self.cli_prefix, cli_prefix, prefix, prepend_cli_prefix, prepend_prefix),
            parser=self.parser,
            keep_short=self.keep_short if keep_short is NOTSET else keep_short,
        )

    def build(self) -> T:
        tp = TyperPrefix(
            env_prefix=self.env_prefix if self.env_prefix is not NOTSET else self.prefix,
            cli_prefix=self.cli_prefix if self.cli_prefix is not NOTSET else self.prefix,
            panel=self.panel,
            keep_short=self.keep_short,
            keep_panels=self.keep_panels,
        )
        return tp.wrap_parser(self.parser)


@dataclasses.dataclass(slots=True, kw_only=True)
class _TyperDataGroup(Generic[T]):
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
        return self.build()

    def _get_parser(self, f: type[Any]) -> None | Callable[..., Any]:
        _parser = self.parsers.get(f, DEFAULT_PARSERS.get(f))
        if _parser is None:
            return None

        def _wrap(v: str) -> Any:
            return _parser[1](v)

        _wrap.__name__ = _parser[0]
        return _wrap

    def make_anno(self, name: str, f: type[Any]) -> Any:
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
