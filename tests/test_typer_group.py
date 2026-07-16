"""Tests for TyperGroup, typer_group decorator, and TyperGroupFromDataclass."""

import dataclasses
import inspect
from datetime import timedelta
from typing import Annotated

import pytest
import typer
from typer_di import Depends

from typer_group_prefix import (
    NOTSET,
    TyperGroup,
    TyperGroupFromDataclass,
    typer_group,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_simple_parser(
    host: Annotated[str, typer.Option("--host", envvar="HOST")] = "localhost",
    port: Annotated[int, typer.Option("--port", envvar="PORT")] = 5432,
) -> dict:
    return {"host": host, "port": port}


def _sig_param_names(depends_obj) -> list[str]:
    """Return the parameter names from the wrapped function inside a Depends."""
    return list(inspect.signature(depends_obj.callback).parameters.keys())


def _sig_param(depends_obj, name: str) -> inspect.Parameter:
    return inspect.signature(depends_obj.callback).parameters[name]


# ---------------------------------------------------------------------------
# TyperGroup construction
# ---------------------------------------------------------------------------

class TestTyperGroupConstruction:
    def test_basic_construction(self):
        g = TyperGroup(panel="P", parser=make_simple_parser)
        assert g.panel == "P"
        assert g.prefix is None
        assert g.keep_short is False
        assert g.keep_panels is False

    def test_construction_with_prefix(self):
        g = TyperGroup(panel="P", prefix="app", parser=make_simple_parser)
        assert g.prefix == "app"

    def test_call_returns_depends(self):
        g = TyperGroup(panel="P", parser=make_simple_parser)
        dep = g()
        assert hasattr(dep, "callback")


# ---------------------------------------------------------------------------
# TyperGroup.build() / parameter name transformation
# ---------------------------------------------------------------------------

class TestTyperGroupBuild:
    def test_no_prefix_keeps_original_names(self):
        g = TyperGroup(panel="P", parser=make_simple_parser)
        dep = g.build()
        names = _sig_param_names(dep)
        assert "host" in names
        assert "port" in names

    def test_prefix_renames_parameters(self):
        g = TyperGroup(panel="P", prefix="db", parser=make_simple_parser)
        dep = g.build()
        names = _sig_param_names(dep)
        assert "db_host" in names
        assert "db_port" in names
        assert "host" not in names
        assert "port" not in names

    def test_prefix_changes_cli_arg_names(self):
        g = TyperGroup(panel="P", prefix="db", parser=make_simple_parser)
        dep = g.build()
        param = _sig_param(dep, "db_host")
        info = param.annotation.__metadata__[0]
        assert isinstance(info, typer.models.OptionInfo)
        assert info.default == "--db-host"

    def test_prefix_changes_envvar(self):
        g = TyperGroup(panel="P", prefix="db", parser=make_simple_parser)
        dep = g.build()
        param = _sig_param(dep, "db_host")
        info = param.annotation.__metadata__[0]
        assert info.envvar == "DB_HOST"

    def test_panel_is_set_on_options(self):
        g = TyperGroup(panel="My Panel", parser=make_simple_parser)
        dep = g.build()
        for pname in _sig_param_names(dep):
            param = _sig_param(dep, pname)
            info = param.annotation.__metadata__[0]
            assert info.rich_help_panel == "My Panel"

    def test_separate_cli_and_env_prefix(self):
        g = TyperGroup(panel="P", cli_prefix="cli", env_prefix="ENV", parser=make_simple_parser)
        dep = g.build()
        param = _sig_param(dep, "cli_host")
        info = param.annotation.__metadata__[0]
        assert info.default == "--cli-host"
        assert info.envvar == "ENV_HOST"


# ---------------------------------------------------------------------------
# TyperGroup.with_options()
# ---------------------------------------------------------------------------

class TestTyperGroupWithOptions:
    def test_returns_new_instance(self):
        g = TyperGroup(panel="P", parser=make_simple_parser)
        g2 = g.with_options(prefix="new")
        assert g is not g2

    def test_original_unchanged(self):
        g = TyperGroup(panel="P", prefix="orig", parser=make_simple_parser)
        g.with_options(prefix="new")
        assert g.prefix == "orig"

    def test_overrides_prefix(self):
        g = TyperGroup(panel="P", prefix="orig", parser=make_simple_parser)
        g2 = g.with_options(prefix="new")
        assert g2.prefix == "new"

    def test_overrides_panel(self):
        g = TyperGroup(panel="Old", parser=make_simple_parser)
        g2 = g.with_options(panel="New")
        assert g2.panel == "New"

    def test_prepend_panel(self):
        g = TyperGroup(panel="DB", parser=make_simple_parser)
        g2 = g.with_options(panel="App", prepend_panel=True)
        assert g2.panel == "App DB"

    def test_prepend_prefix(self):
        g = TyperGroup(panel="P", prefix="db", parser=make_simple_parser)
        g2 = g.with_options(prefix="app", prepend_prefix=True)
        assert g2.prefix == "app-db"

    def test_inherits_keep_short(self):
        g = TyperGroup(panel="P", keep_short=True, parser=make_simple_parser)
        g2 = g.with_options(prefix="x")
        assert g2.keep_short is True

    def test_overrides_keep_short(self):
        g = TyperGroup(panel="P", keep_short=True, parser=make_simple_parser)
        g2 = g.with_options(keep_short=False)
        assert g2.keep_short is False

    def test_inherits_keep_panels(self):
        """Regression: with_options() must preserve keep_panels (was a bug)."""
        g = TyperGroup(panel="P", keep_panels=True, parser=make_simple_parser)
        g2 = g.with_options(prefix="x")
        assert g2.keep_panels is True

    def test_overrides_keep_panels(self):
        g = TyperGroup(panel="P", keep_panels=True, parser=make_simple_parser)
        g2 = g.with_options(keep_panels=False)
        assert g2.keep_panels is False

    def test_separate_env_cli_prefix_override(self):
        g = TyperGroup(panel="P", prefix="base", parser=make_simple_parser)
        g2 = g.with_options(cli_prefix="cli", env_prefix="ENV")
        dep = g2.build()
        param = _sig_param(dep, "cli_host")
        info = param.annotation.__metadata__[0]
        assert info.default == "--cli-host"
        assert info.envvar == "ENV_HOST"


# ---------------------------------------------------------------------------
# @typer_group decorator
# ---------------------------------------------------------------------------

class TestTyperGroupDecorator:
    def test_decorator_creates_typer_group(self):
        @typer_group(panel="P")
        def parse(host: Annotated[str, typer.Option("--host", envvar="HOST")] = "lo") -> str:
            return host

        assert isinstance(parse, TyperGroup)

    def test_decorator_preserves_parser(self):
        @typer_group(panel="P")
        def parse(host: Annotated[str, typer.Option("--host", envvar="HOST")] = "lo") -> str:
            return host

        assert parse.parser.__name__ == "parse"

    def test_decorator_with_prefix(self):
        @typer_group(panel="P", prefix="db")
        def parse(host: Annotated[str, typer.Option("--host", envvar="HOST")] = "lo") -> str:
            return host

        dep = parse.build()
        assert "db_host" in _sig_param_names(dep)


# ---------------------------------------------------------------------------
# TyperGroupFromDataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass(kw_only=True)
class SimpleConfig:
    host: str = "localhost"
    port: int = 5432


@dataclasses.dataclass(kw_only=True)
class TimedeltaConfig:
    timeout: timedelta = timedelta(seconds=30)


class TestTyperGroupFromDataclass:
    def test_returns_typer_group(self):
        g = TyperGroupFromDataclass(SimpleConfig, panel="P")
        assert isinstance(g, TyperGroup)

    def test_generates_option_for_each_field(self):
        g = TyperGroupFromDataclass(SimpleConfig, panel="P")
        dep = g.build()
        names = _sig_param_names(dep)
        assert "host" in names
        assert "port" in names

    def test_field_has_option_info(self):
        g = TyperGroupFromDataclass(SimpleConfig, panel="P")
        dep = g.build()
        param = _sig_param(dep, "host")
        assert any(isinstance(m, typer.models.OptionInfo) for m in param.annotation.__metadata__)

    def test_cli_arg_matches_field_name(self):
        g = TyperGroupFromDataclass(SimpleConfig, panel="P")
        dep = g.build()
        param = _sig_param(dep, "host")
        info = param.annotation.__metadata__[0]
        assert info.default == "--host"

    def test_envvar_matches_field_name_uppercase(self):
        g = TyperGroupFromDataclass(SimpleConfig, panel="P")
        dep = g.build()
        param = _sig_param(dep, "host")
        info = param.annotation.__metadata__[0]
        assert info.envvar == "HOST"

    def test_with_prefix_renames_params(self):
        g = TyperGroupFromDataclass(SimpleConfig, panel="P")
        dep = g.with_options(prefix="db").build()
        names = _sig_param_names(dep)
        assert "db_host" in names
        assert "db_port" in names

    def test_timedelta_field_gets_parser(self):
        g = TyperGroupFromDataclass(TimedeltaConfig, panel="P")
        dep = g.build()
        param = _sig_param(dep, "timeout")
        info = param.annotation.__metadata__[0]
        assert info.parser is not None
        assert info.parser("60") == timedelta(seconds=60)

    def test_custom_parser_registered(self):
        class Colour:
            def __init__(self, v: str):
                self.v = v

        @dataclasses.dataclass(kw_only=True)
        class ColourConfig:
            bg: Colour = dataclasses.field(default_factory=lambda: Colour("white"))

        g = TyperGroupFromDataclass(
            ColourConfig,
            panel="P",
            parsers={Colour: ("COLOUR", Colour)},
        )
        dep = g.build()
        param = _sig_param(dep, "bg")
        info = param.annotation.__metadata__[0]
        assert info.parser is not None

    def test_secretstr_field_has_prompt(self):
        try:
            from pydantic import SecretStr
        except ImportError:
            pytest.skip("pydantic not installed")

        @dataclasses.dataclass(kw_only=True)
        class SecretConfig:
            password: SecretStr = SecretStr("")

        g = TyperGroupFromDataclass(SecretConfig, panel="P")
        dep = g.build()
        param = _sig_param(dep, "password")
        info = param.annotation.__metadata__[0]
        assert info.prompt is True
        assert info.hide_input is True


# ---------------------------------------------------------------------------
# Nested Depends (wrap_parser recursion)
# ---------------------------------------------------------------------------

def parse_inner(
    host: Annotated[str, typer.Option("--host", envvar="HOST")] = "localhost",
) -> str:
    return host


INNER = TyperGroup(panel="P", parser=parse_inner)


def parse_outer(
    inner: str = INNER(),
) -> dict:
    return {"inner": inner}


class TestNestedDepends:
    def test_nested_depends_is_transformed(self):
        """wrap_parser must recursively prefix nested Depends parameters."""
        g = TyperGroup(panel="P", prefix="outer", parser=parse_outer)
        dep = g.build()
        # The outer wrapper's callback should have the nested dep, not raw "inner"
        sig = inspect.signature(dep.callback)
        # "inner" is a Depends – its name should be unchanged (it's a dep, not an option)
        assert "inner" in sig.parameters
