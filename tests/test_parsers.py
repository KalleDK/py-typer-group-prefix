"""Tests for typer_group_prefix.parsers."""

import pytest
from datetime import timedelta

from typer_group_prefix.parsers import parse_dt, DEFAULT_PARSERS


class TestParseDt:
    """Tests for parse_dt()."""

    def test_seconds_only(self):
        assert parse_dt("90") == timedelta(seconds=90)

    def test_seconds_zero(self):
        assert parse_dt("0") == timedelta(seconds=0)

    def test_minutes_and_seconds(self):
        assert parse_dt("1:30") == timedelta(minutes=1, seconds=30)

    def test_minutes_and_seconds_zero_seconds(self):
        assert parse_dt("2:00") == timedelta(minutes=2)

    def test_hours_minutes_seconds(self):
        assert parse_dt("1:02:03") == timedelta(hours=1, minutes=2, seconds=3)

    def test_hours_minutes_seconds_zeros(self):
        assert parse_dt("0:00:00") == timedelta(0)

    def test_passthrough_timedelta(self):
        td = timedelta(hours=3)
        assert parse_dt(td) is td

    def test_large_seconds(self):
        assert parse_dt("3600") == timedelta(hours=1)

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            parse_dt("not-a-number")

    def test_invalid_minutes_component_raises(self):
        with pytest.raises(ValueError):
            parse_dt("abc:00")


class TestDefaultParsers:
    """Tests for DEFAULT_PARSERS registry."""

    def test_timedelta_registered(self):
        assert timedelta in DEFAULT_PARSERS

    def test_timedelta_entry_structure(self):
        metavar, fn = DEFAULT_PARSERS[timedelta]
        assert isinstance(metavar, str)
        assert callable(fn)

    def test_timedelta_parser_works(self):
        _, fn = DEFAULT_PARSERS[timedelta]
        assert fn("60") == timedelta(seconds=60)

    def test_pydantic_secretstr_registered_if_available(self):
        try:
            from pydantic import SecretStr

            assert SecretStr in DEFAULT_PARSERS
        except ImportError:
            pytest.skip("pydantic not installed")
