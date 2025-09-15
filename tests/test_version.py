import pathlib
import tomllib

from typer_group_prefix import __version__


def test_version_project():
    with pathlib.Path("pyproject.toml").open("rb") as fp:
        config = tomllib.load(fp)
    assert __version__ == config["project"]["version"]


def test_version_bump():
    with pathlib.Path(".bumpversion.toml").open("rb") as fp:
        config = tomllib.load(fp)

    assert __version__ == config["tool"]["bumpversion"]["current_version"]
