import dataclasses
import enum
import logging
import os
import pathlib
import urllib
import urllib.parse
from datetime import timedelta
from typing import Annotated, TypeGuard

import dotenv
import typer
from rich.console import Console
from typer_di import Depends, TyperDI

from typer_group_prefix import (
    TyperGroup,
    __version__,
)


class Scheme(enum.StrEnum):
    """
    Define an enumeration `Scheme` that inherits from `enum.StrEnum`
    This enumeration represents the scheme part of a URL, which specifies the protocol to be used
    In this case, the two most common protocols are represented: HTTP and HTTPS
    """

    HTTPS = "https"
    """Represents the HTTPS protocol, which is HTTP with additional security (SSL/TLS)"""
    HTTP = "http"
    """Represents the HTTP protocol, which is used for transmitting hypertext over the World Wide Web"""


SECOND = timedelta(seconds=1)
MINUTE = SECOND * 60

# Config
DEFAULT_HTTPX_TIMEOUT = 2 * MINUTE
DEFAULT_URL_SCHEME = Scheme.HTTPS
DEFAULT_URL_PATH = "/"
DEFAULT_CAPATH: pathlib.Path | None = None
DEFAULT_INSECURE = False
DEFAULT_DISABLE_NEGOTIATE = False

# Cli Env
DEFAULT_PREFIX = "SCEP"
DEFAULT_PANEL = "SCEP Config"


def is_scheme(scheme: str) -> TypeGuard[Scheme]:
    return scheme in {"http", "https"}


@dataclasses.dataclass(slots=True, kw_only=True)
class Config:
    scheme: Scheme = DEFAULT_URL_SCHEME
    hostname: str
    port: int | None = None
    path: str = DEFAULT_URL_PATH
    username: str
    password: str
    capath: pathlib.Path | None = DEFAULT_CAPATH
    insecure: bool = DEFAULT_INSECURE
    disable_negotiate: bool = DEFAULT_DISABLE_NEGOTIATE
    timeout: timedelta = DEFAULT_HTTPX_TIMEOUT


def _config_parser(
    scep_server: Annotated[
        str,
        typer.Option(
            "-s",
            "--server",
            envvar="SERVER",
            show_default=False,
        ),
    ] = "s",
    scep_username: Annotated[
        str,
        typer.Option(
            "-u",
            "--username",
            envvar="USERNAME",
            show_default=False,
        ),
    ] = "u",
    scep_password: Annotated[
        str,
        typer.Option(
            "-p",
            "--password",
            envvar="PASSWORD",
            prompt=True,
            hide_input=True,
            parser=str,
            show_default=False,
        ),
    ] = "p",
    scep_insecure: Annotated[
        bool,
        typer.Option(
            "-k",
            "--insecure",
            envvar="INSECURE",
        ),
    ] = DEFAULT_INSECURE,
    scep_capath: Annotated[
        pathlib.Path | None,
        typer.Option(
            "--capath",
            envvar="CAPATH",
            exists=True,
            file_okay=False,
            readable=True,
        ),
    ] = DEFAULT_CAPATH,
) -> Config:
    parts = urllib.parse.urlsplit(scep_server)
    if not is_scheme(parts.scheme):
        parts = urllib.parse.urlsplit(
            f"{DEFAULT_URL_SCHEME}://{scep_server}{DEFAULT_URL_PATH}"
        )

    if not is_scheme(parts.scheme):
        raise ValueError("Invalid server url")

    if parts.netloc == "":
        raise ValueError("Invalid server url")

    netloc_parts = parts.netloc.split(":", 1)
    if len(netloc_parts) == 2:
        hostname, port = netloc_parts
        _port = int(port)
    else:
        hostname = netloc_parts[0]
        _port = None

    return Config(
        scheme=Scheme(parts.scheme),
        hostname=hostname,
        port=_port,
        username=scep_username,
        password=scep_password,
        insecure=scep_insecure,
        capath=scep_capath,
        path=parts.path,
    )


CLI_CONFIG = TyperGroup(
    panel=DEFAULT_PANEL,
    env_prefix=DEFAULT_PREFIX,
    parser=_config_parser,
)


class LogLevel(enum.IntEnum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


def get_logging(
    verbose: Annotated[int, typer.Option("--verbose", "-v", count=True)] = 0,
    quiet: Annotated[bool, typer.Option("--quiet")] = False,
) -> None | LogLevel:
    if quiet:
        return None

    if verbose == 0:
        level = LogLevel.WARNING
    elif verbose == 1:
        level = LogLevel.INFO
    else:
        level = LogLevel.DEBUG
    logging.basicConfig(level=level)

    return level


def make_typer(
    *,
    panel: str | None = None,
    prefix: str | None = None,
    env_prefix: str | None = None,
) -> TyperDI:
    app = TyperDI()

    @app.command("new")
    def cli_new(  # pyright: ignore[reportUnusedFunction]
        config: Config = CLI_CONFIG.with_options(prefix="abc", panel="PANEL_NEW")(),
        server: str = "fds",
        _logging: None | int = Depends(get_logging),
    ) -> None:
        console = Console()
        console.print(config)

    @app.command("version")
    def cli_version(  # pyright: ignore[reportUnusedFunction]
        config: Config = CLI_CONFIG(),
        config2: Config = CLI_CONFIG.with_options(prefix="qwe", panel="PANEL_NEW")(),
        _logging: None | LogLevel = Depends(get_logging),
    ):
        """Show the version."""
        console = Console()
        logging.getLogger(__name__).debug("Showing version")
        console.print(f"[bold]Version:[/bold] {__version__} at {_logging!r}")
        console.print(config)
        console.print(config2)

    return app


def main():
    dotenv.load_dotenv()
    prefix = os.environ.get("SCEP_PREFIX", None)
    env_prefix = os.environ.get("SCEP_ENV_PREFIX", None)
    panel = os.environ.get("SCEP_PANEL", None)

    cli = make_typer(prefix=prefix, panel=panel, env_prefix=env_prefix)

    cli()


if __name__ == "__main__":
    main()
