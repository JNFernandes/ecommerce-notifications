"""Tests for the system clock and id generator."""

from datetime import UTC

from notifications.infrastructure.system import SystemClock, Uuid4Generator


def test_should_return_the_current_time_in_utc() -> None:
    now = SystemClock().now()

    assert now.tzinfo is UTC


def test_should_generate_distinct_version_4_ids() -> None:
    generator = Uuid4Generator()

    first, second = generator.new_id(), generator.new_id()

    assert first != second
    assert first.version == 4
