"""Tests for TyperPrefix – the low-level option transformer."""

import pytest
import typer

from typer_group_prefix import TyperPrefix, NOTSET


def make_option(primary: str, *extra: str, envvar: str | None = None, panel: str | None = None) -> typer.models.OptionInfo:
    """Helper: build a OptionInfo with the given arg names and optional envvar."""
    opt = typer.Option(primary, *extra, envvar=envvar)
    if panel is not None:
        opt.rich_help_panel = panel
    return opt


# ---------------------------------------------------------------------------
# Construction / normalisation
# ---------------------------------------------------------------------------

class TestTyperPrefixNormalisation:
    def test_cli_prefix_normalised_to_lowercase_hyphens(self):
        tp = TyperPrefix(cli_prefix="My_Prefix", env_prefix=None, panel="P")
        assert tp.cli_prefix == "my-prefix"

    def test_cli_prefix_strips_leading_trailing_hyphens(self):
        tp = TyperPrefix(cli_prefix="--my-prefix--", env_prefix=None, panel="P")
        assert tp.cli_prefix == "my-prefix"

    def test_cli_prefix_empty_string_becomes_none(self):
        tp = TyperPrefix(cli_prefix="", env_prefix=None, panel="P")
        assert tp.cli_prefix is None

    def test_cli_prefix_whitespace_only_becomes_none(self):
        tp = TyperPrefix(cli_prefix="   ", env_prefix=None, panel="P")
        assert tp.cli_prefix is None

    def test_env_prefix_normalised_to_uppercase_underscores(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="my-prefix", panel="P")
        assert tp.env_prefix == "MY_PREFIX"

    def test_env_prefix_strips_leading_trailing_underscores(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="__MY_PREFIX__", panel="P")
        assert tp.env_prefix == "MY_PREFIX"

    def test_env_prefix_empty_string_becomes_none(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="", panel="P")
        assert tp.env_prefix is None

    def test_none_prefixes_stay_none(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="P")
        assert tp.cli_prefix is None
        assert tp.env_prefix is None


# ---------------------------------------------------------------------------
# make_env
# ---------------------------------------------------------------------------

class TestMakeEnv:
    def test_none_envvar_returns_none(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="APP", panel="P")
        opt = make_option("--host")  # no envvar
        assert tp.make_env(opt) is None

    def test_single_envvar_no_prefix(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="P")
        opt = make_option("--host", envvar="host")
        assert tp.make_env(opt) == "HOST"

    def test_single_envvar_with_prefix(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="APP", panel="P")
        opt = make_option("--host", envvar="HOST")
        assert tp.make_env(opt) == "APP_HOST"

    def test_multiple_envvars_no_prefix(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="P")
        opt = make_option("--host", envvar=("host", "hostname"))  # type: ignore[arg-type]
        result = tp.make_env(opt)
        assert result == ["HOST", "HOSTNAME"]

    def test_multiple_envvars_with_prefix(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="APP", panel="P")
        opt = make_option("--host", envvar=("host", "hostname"))  # type: ignore[arg-type]
        result = tp.make_env(opt)
        assert result == ["APP_HOST", "APP_HOSTNAME"]


# ---------------------------------------------------------------------------
# make_args
# ---------------------------------------------------------------------------

class TestMakeArgs:
    def test_no_prefix_returns_long_args_only(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="P")
        opt = make_option("--host", "--hostname", "-H")
        args = tp.make_args(opt)
        assert "--host" in args
        assert "--hostname" in args
        assert "-H" not in args

    def test_no_prefix_keep_short_includes_short(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="P", keep_short=True)
        opt = make_option("--host", "-H")
        args = tp.make_args(opt)
        assert "-H" in args

    def test_with_prefix_long_args_prefixed(self):
        tp = TyperPrefix(cli_prefix="app", env_prefix=None, panel="P")
        opt = make_option("--host")
        args = tp.make_args(opt)
        assert args == ("--app-host",)

    def test_with_prefix_short_dropped_by_default(self):
        tp = TyperPrefix(cli_prefix="app", env_prefix=None, panel="P")
        opt = make_option("--host", "-H")
        args = tp.make_args(opt)
        assert all(a.startswith("--app-") for a in args)

    def test_with_prefix_keep_short_converts_short_to_long(self):
        tp = TyperPrefix(cli_prefix="app", env_prefix=None, panel="P", keep_short=True)
        opt = make_option("--host", "-H")
        args = tp.make_args(opt)
        assert "--app-H" in args

    def test_raises_when_no_args_remain(self):
        # Only short arg, keep_short=False, prefix set → nothing survives the filter
        tp = TyperPrefix(cli_prefix="app", env_prefix=None, panel="P", keep_short=False)
        opt = typer.Option("-H")  # only a short flag; default is "-H"
        # get_args will return ("-H",); after filter (startswith "--") → empty
        with pytest.raises(ValueError, match="No args generated"):
            tp.make_args(opt)


# ---------------------------------------------------------------------------
# update_info
# ---------------------------------------------------------------------------

class TestUpdateInfo:
    def test_sets_panel(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="My Panel")
        opt = make_option("--host", envvar="HOST")
        tp.update_info(opt)
        assert opt.rich_help_panel == "My Panel"

    def test_keeps_existing_panel_when_keep_panels(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="New Panel", keep_panels=True)
        opt = make_option("--host", envvar="HOST", panel="Old Panel")
        tp.update_info(opt)
        assert opt.rich_help_panel == "Old Panel"

    def test_overrides_existing_panel_when_not_keep_panels(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix=None, panel="New Panel", keep_panels=False)
        opt = make_option("--host", envvar="HOST", panel="Old Panel")
        tp.update_info(opt)
        assert opt.rich_help_panel == "New Panel"

    def test_returns_python_safe_name(self):
        tp = TyperPrefix(cli_prefix="my-app", env_prefix=None, panel="P")
        opt = make_option("--host", envvar="HOST")
        new_name = tp.update_info(opt)
        # "--my-app-host" → "my_app_host"
        assert new_name == "my_app_host"

    def test_updates_envvar(self):
        tp = TyperPrefix(cli_prefix=None, env_prefix="APP", panel="P")
        opt = make_option("--host", envvar="HOST")
        tp.update_info(opt)
        assert opt.envvar == "APP_HOST"
