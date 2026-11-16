"""Tests for the message templates."""

from decimal import Decimal
from uuid import UUID

import pytest

from notifications.domain.errors import (
    InvalidAmountError,
    InvalidCurrencyError,
    InvalidOrderTotalError,
)
from notifications.domain.messages import (
    MAX_REASON_LENGTH,
    format_amount,
    normalise_reason,
    order_cancelled_message,
    order_confirmed_message,
    order_received_message,
    payment_refunded_message,
)

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
    with pytest.raises(InvalidAmountError):
        format_amount(Decimal(amount))


def test_should_still_report_an_invalid_order_total_as_such() -> None:
    with pytest.raises(InvalidOrderTotalError):
        order_received_message(ORDER_ID, Decimal("-1"))


def test_should_treat_an_invalid_order_total_as_an_invalid_amount() -> None:
    assert issubclass(InvalidOrderTotalError, InvalidAmountError)


def test_should_build_the_order_received_text_from_the_template() -> None:
    message = order_received_message(ORDER_ID, Decimal("129.9"))

    assert message == "Your order 0d1e2f3a-0000-4000-8000-0000000000aa for 129.90 was received."


def test_should_produce_the_same_text_for_the_same_input() -> None:
    assert order_received_message(ORDER_ID, Decimal("5")) == order_received_message(
        ORDER_ID, Decimal("5.00")
    )


def test_should_build_the_order_confirmed_text_from_the_template() -> None:
    assert (
        order_confirmed_message(ORDER_ID)
        == "Your order 0d1e2f3a-0000-4000-8000-0000000000aa was confirmed."
    )


def test_should_produce_the_same_confirmed_text_every_time() -> None:
    assert order_confirmed_message(ORDER_ID) == order_confirmed_message(ORDER_ID)


@pytest.mark.parametrize("reason", [None, "", "   ", "\n\t", "."])
def test_should_use_the_generic_cancelled_text_when_there_is_no_reason(
    reason: str | None,
) -> None:
    assert order_cancelled_message(ORDER_ID, reason) == (
        "Your order 0d1e2f3a-0000-4000-8000-0000000000aa was cancelled."
    )


def test_should_show_the_reason_of_a_cancellation() -> None:
    assert order_cancelled_message(ORDER_ID, "payment failed: amount exceeds the threshold") == (
        "Your order 0d1e2f3a-0000-4000-8000-0000000000aa was cancelled: "
        "payment failed: amount exceeds the threshold."
    )


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("  stock rejected: insufficient stock.  ", "stock rejected: insufficient stock"),
        ("a\r\nb\tc", "a b c"),
        ("line one\r\n\r\nline two", "line one line two"),
        ("card expired...", "card expired.."),
        ("payment failed", "payment failed"),
    ],
)
def test_should_normalise_the_reason(raw: str, shown: str) -> None:
    assert normalise_reason(raw) == shown


def test_should_keep_a_reason_of_exactly_the_maximum_length() -> None:
    reason = "x" * MAX_REASON_LENGTH

    assert normalise_reason(reason) == reason


def test_should_cut_a_longer_reason_and_mark_the_cut() -> None:
    assert normalise_reason("x" * (MAX_REASON_LENGTH + 1)) == "x" * MAX_REASON_LENGTH + "…"


def test_should_produce_the_same_cancelled_text_every_time() -> None:
    reason = "  payment failed:\n card declined. "

    assert order_cancelled_message(ORDER_ID, reason) == order_cancelled_message(ORDER_ID, reason)


@pytest.mark.parametrize(
    ("amount", "currency", "shown"),
    [
        ("129.9", "EUR", "129.90 EUR"),
        ("0", "USD", "0.00 USD"),
        ("10.005", "EUR", "10.01 EUR"),
        ("123456789012345678.123456789", "EUR", "123456789012345678.12 EUR"),
    ],
)
def test_should_show_the_refunded_amount_with_two_decimals_and_the_currency(
    amount: str, currency: str, shown: str
) -> None:
    assert payment_refunded_message(ORDER_ID, Decimal(amount), currency) == (
        f"Your payment of {shown} for order 0d1e2f3a-0000-4000-8000-0000000000aa was refunded."
    )


@pytest.mark.parametrize("currency", ["eur", "EURO", "", "E1R", "EUR\n"])
def test_should_reject_a_currency_that_is_not_three_uppercase_letters(currency: str) -> None:
    with pytest.raises(InvalidCurrencyError):
        payment_refunded_message(ORDER_ID, Decimal("1"), currency)


def test_should_reject_a_negative_refund_amount() -> None:
    with pytest.raises(InvalidAmountError):
        payment_refunded_message(ORDER_ID, Decimal("-1"), "EUR")


def test_should_produce_the_same_refund_text_every_time() -> None:
    assert payment_refunded_message(ORDER_ID, Decimal("5"), "EUR") == payment_refunded_message(
        ORDER_ID, Decimal("5.00"), "EUR"
    )
