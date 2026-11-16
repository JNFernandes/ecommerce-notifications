"""Tests for the message templates."""

from decimal import Decimal
from uuid import UUID

import pytest

from notifications.domain.errors import InvalidOrderTotalError
from notifications.domain.messages import format_amount, order_received_message

ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-0000000000aa")


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        ("129.9", "129.90"),
        ("0", "0.00"),
        ("10.005", "10.01"),
        ("10.004", "10.00"),
        ("1E+2", "100.00"),
        ("123456789012345678.123456789", "123456789012345678.12"),
    ],
)
def test_should_write_the_amount_with_two_decimals_rounded_half_up(
    amount: str, expected: str
) -> None:
    assert format_amount(Decimal(amount)) == expected


@pytest.mark.parametrize("amount", ["-0.01", "NaN", "Infinity"])
def test_should_reject_a_negative_or_non_finite_amount(amount: str) -> None:
    with pytest.raises(InvalidOrderTotalError):
        format_amount(Decimal(amount))


def test_should_build_the_order_received_text_from_the_template() -> None:
    message = order_received_message(ORDER_ID, Decimal("129.9"))

    assert message == "Your order 0d1e2f3a-0000-4000-8000-0000000000aa for 129.90 was received."


def test_should_produce_the_same_text_for_the_same_input() -> None:
    assert order_received_message(ORDER_ID, Decimal("5")) == order_received_message(
        ORDER_ID, Decimal("5.00")
    )
