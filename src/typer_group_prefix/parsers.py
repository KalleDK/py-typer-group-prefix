from datetime import timedelta
from typing import Any, Callable


def parse_dt(v: str | timedelta) -> timedelta:
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


DEFAULT_PARSERS: dict[type, tuple[str, Callable[[str], Any]]] = {
    timedelta: ("SEC", parse_dt),
}


try:
    from pydantic import SecretStr

    DEFAULT_PARSERS[SecretStr] = ("TEXT", SecretStr)
except ImportError:
    pass
