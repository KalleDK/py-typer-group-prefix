import copy
import dataclasses
from collections.abc import Callable, Sequence
from functools import wraps
from typing import (
    Any,
    Generic,
    NamedTuple,
    Self,
    TypeVar,
)

import typer
import typer_di

import inspect

__version__ = "0.1.2"


class TyperPrefix(NamedTuple):
    cli_val: str | None
    env_val: str | None
    panel: str

    @staticmethod
    def clean_prefix(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value == "":
            return None
        return value

    @staticmethod
    def clean_env_prefix(value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()

    def env(self, value: str | Sequence[str] | None):
        if value is None:
            return None
        if self.env_val is None:
            if isinstance(value, str):
                return value.upper()
            return [v.upper() for v in value]

        if isinstance(value, str):
            return f"{self.env_val}{value.upper()}"
        return [f"{self.env_val}{v.upper()}" for v in value]

    def args(self, value: Any | None, values: Sequence[str] | None):
        if not isinstance(value, str):
            raise ValueError("Only str allowed for args")

        values = tuple(values or ())
        if self.cli_val is None:
            return value, values

        values = tuple(x for x in (value, *values) if x.startswith("--"))
        if not values:
            raise ValueError("No long args found")

        parsed_values = tuple(f"--{self.cli_val}{value[2:]}" for value in values)
        return parsed_values[-1].replace("-", "_").lower().strip("_"), parsed_values[0], parsed_values[1:]

    @classmethod
    def from_prefix(
        cls,
        prefix: str | None,
        extra_env_prefix: str | None,
        default_prefix: str | None,
        panel: str | None,
        default_panel: str,
    ) -> Self:
        prefix = TyperPrefix.clean_prefix(prefix)
        extra_env_prefix = TyperPrefix.clean_env_prefix(extra_env_prefix)

        cli_prefix = None
        env_prefix = ""

        if prefix is not None:
            _prefix = prefix + "_"
            cli_prefix = _prefix.replace("_", "-").lower()
            env_prefix = _prefix.upper()
        if extra_env_prefix is not None:
            if extra_env_prefix != "":
                extra_env_prefix = extra_env_prefix.upper() + "_"
            env_prefix = extra_env_prefix + env_prefix
        elif default_prefix is not None:
            if default_prefix.upper() != (prefix or "").upper():
                _default_prefix = default_prefix.upper() + "_"
                env_prefix = _default_prefix + env_prefix

        return cls(
            cli_val=cli_prefix,
            env_val=None if env_prefix == "" else env_prefix,
            panel=panel if panel is not None else default_panel,
        )


T = TypeVar("T")

_prefixes_nr = 0

@dataclasses.dataclass(slots=True)
class TyperArgsGroup(Generic[T]):
    default_panel: str
    default_prefix: str
    parser: Callable[..., T]
    panel: str | None = None
    prefix: str | None = None
    extra_env_prefix: str | None = None

    def __call__(self) -> T:
        return self.build()

    def with_options(
        self,
        *,
        panel: str | None = None,
        prefix: str | None = None,
        extra_env_prefix: str | None = None,
    ) -> T:
        return TyperArgsGroup(
            default_panel=self.default_panel,
            default_prefix=self.default_prefix,
            parser=self.parser,
            panel=panel,
            prefix=prefix,
            extra_env_prefix=extra_env_prefix,
        ).build()

    def build(self) -> T:

        tp = TyperPrefix.from_prefix(
            prefix=self.prefix,
            extra_env_prefix=self.extra_env_prefix,
            default_prefix=self.default_prefix,
            panel=self.panel,
            default_panel=self.default_panel,
        )
        
        signature = copy.deepcopy(inspect.signature(self.parser))
        

        parameters = []
        
        kwargs_new_names: dict[str, str] = {}
        
        for parameter in signature.parameters.values():
            for info in parameter.annotation.__metadata__:
                if isinstance(info, typer.models.OptionInfo):
                    info.rich_help_panel = tp.panel
                    kw_name, info.default, info.param_decls = tp.args(
                        info.default, info.param_decls
                    )
                    info.envvar = tp.env(info.envvar)
                    break
            else:
                raise ValueError(f"No Option found for {parameter.name} in {self.parser}")
            kwargs_new_names[kw_name] = parameter.name
            parameter = parameter.replace(name=kw_name)
            parameters.append(parameter)
        
        signature = signature.replace(parameters=parameters)
        

        @wraps(self.parser)
        def _wrapper(*args: Any, **kwargs: Any) -> T:
            kwargs = {kwargs_new_names[kw]: val for kw, val in kwargs.items()}
            return self.parser(*args, **kwargs)
        
        setattr(_wrapper, "__signature__", signature)
        delattr(_wrapper, "__wrapped__")
        
        return typer_di.Depends(_wrapper)
