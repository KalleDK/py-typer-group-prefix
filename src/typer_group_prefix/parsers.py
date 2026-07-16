"""Built-in parsers for common types used with typer options.

This module provides string-to-type converters (parsers) that can be used
when a CLI option needs a type not natively supported by typer, such as
:class:`datetime.timedelta`.  It also registers optional pydantic parsers
when pydantic is installed.
"""

from datetime import timedelta
from typing import Any, Callable


def parse_dt(v: str | timedelta) -> timedelta:
    """Parse a string into a :class:`datetime.timedelta`.

    Accepts three colon-separated formats:

    * ``"SS"`` – plain integer seconds, e.g. ``"90"`` → 90 seconds
    * ``"MM:SS"`` – minutes and seconds, e.g. ``"1:30"`` → 90 seconds
    * ``"HH:MM:SS"`` – hours, minutes and seconds, e.g. ``"1:30:00"`` → 5 400 seconds

    If *v* is already a :class:`~datetime.timedelta` it is returned unchanged.

    Args:
        v: The value to parse – either a pre-built timedelta or a colon-delimited string.

    Returns:
        A :class:`datetime.timedelta` representing the duration.

    Raises:
        ValueError: If the string cannot be converted to integers.
    """
    if isinstance(v, timedelta):
        return v
    parts = v.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return timedelta(
            hours=int(hours),
            minutes=int(minutes),
            seconds=int(seconds),
        )
    elif len(parts) == 2:
        minutes, seconds = parts
        return timedelta(
            minutes=int(minutes),
            seconds=int(seconds),
        )
    return timedelta(seconds=int(v))


#: Registry mapping Python types to ``(metavar, parser)`` pairs used by
#: :class:`~typer_group_prefix.TyperGroupFromDataclass` when building CLI
#: options for dataclass fields.  Populated at import time; pydantic's
#: ``SecretStr`` is added automatically when pydantic is available.
DEFAULT_PARSERS: dict[type, tuple[str, Callable[[str], Any]]] = {
    timedelta: ("SEC", parse_dt),
}


try:
    from pydantic import SecretStr

    DEFAULT_PARSERS[SecretStr] = ("TEXT", SecretStr)
except ImportError:
    pass
