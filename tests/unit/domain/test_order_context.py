"""Tests for the OrderContext aggregate."""

import dataclasses
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.domain.errors import InvalidOrderTotalError, NaiveTimestampError
from notifications.domain.order_context import OrderContext

ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-0000000000aa")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
NOW = datetime(2026, 11, 16, 10, 0, 0, 999_999, tzinfo=UTC)


def _context(total: str = "129.90", customer: UUID = CUSTOMER_ID) -> OrderContext:
    return OrderContext.from_order_placed(
        order_id=ORDER_ID, customer_id=customer, total_amount=Decimal(total), now=NOW
    )


def test_should_keep_the_exact_total_and_record_the_time_in_utc_milliseconds() -> None:
    context = _context("123456789012345678.123456789")

    assert context.order_id == ORDER_ID
    assert context.customer_id == CUSTOMER_ID
    assert context.total_amount == Decimal("123456789012345678.123456789")
    assert context.recorded_at == datetime(2026, 11, 16, 10, 0, 0, 999_000, tzinfo=UTC)


def test_should_reject_a_negative_total() -> None:
    with pytest.raises(InvalidOrderTotalError):
        _context("-5")


def test_should_reject_a_recording_time_without_timezone() -> None:
    with pytest.raises(NaiveTimestampError):
        OrderContext.from_order_placed(
            order_id=ORDER_ID,
            customer_id=CUSTOMER_ID,
            total_amount=Decimal(1),
            now=datetime(2026, 11, 16),
        )


def test_should_not_conflict_with_the_same_values() -> None:
    assert _context().conflicts_with(_context()) is False


def test_should_treat_numerically_equal_totals_as_the_same() -> None:
    assert _context("129.9").conflicts_with(_context("129.90")) is False


def test_should_ignore_the_recording_time_when_comparing() -> None:
    later = dataclasses.replace(_context(), recorded_at=NOW + timedelta(days=1))

    assert _context().conflicts_with(later) is False


def test_should_conflict_when_the_customer_differs() -> None:
    other = _context(customer=UUID("c1c2c3c4-0000-4000-8000-0000000000dd"))

    assert _context().conflicts_with(other) is True


def test_should_conflict_when_the_total_differs() -> None:
    assert _context("129.90").conflicts_with(_context("130.00")) is True
