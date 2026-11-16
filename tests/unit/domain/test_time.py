"""Tests for the shared timestamp rule."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from notifications.domain.errors import NaiveTimestampError
from notifications.domain.time import require_utc_millis


def test_should_truncate_a_timestamp_to_milliseconds() -> None:
    value = datetime(2026, 11, 16, 10, 0, 0, 123_987, tzinfo=UTC)

    assert require_utc_millis(value) == datetime(2026, 11, 16, 10, 0, 0, 123_000, tzinfo=UTC)


def test_should_convert_an_offset_timestamp_to_utc() -> None:
    value = datetime(2026, 11, 16, 12, 0, tzinfo=timezone(timedelta(hours=2)))

    result = require_utc_millis(value)

    assert result == datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    assert result.tzinfo is UTC


def test_should_reject_a_timestamp_without_timezone() -> None:
    with pytest.raises(NaiveTimestampError):
        require_utc_millis(datetime(2026, 11, 16, 10, 0))
