"""Tests for parsing ``payments.payment-refunded`` messages."""

from decimal import Decimal
from uuid import UUID

import pytest

from notifications.application.record_payment_refunded import PaymentRefundedCommand
from notifications.messaging.messages import InvalidMessageError
from notifications.messaging.payment_refunded import parse_payment_refunded
from tests.support.events import MISSING, payment_refunded_payload, to_json_bytes
from tests.support.fakes import consumed_message


def _parse(**overrides: object) -> PaymentRefundedCommand:
    payload = payment_refunded_payload(**overrides)
    return parse_payment_refunded(consumed_message(value=to_json_bytes(payload)))


def _reason(**overrides: object) -> str:
    with pytest.raises(InvalidMessageError) as raised:
        _parse(**overrides)
    return raised.value.reason


def test_should_identify_the_order_by_order_id_not_by_the_payment_id() -> None:
    payload = payment_refunded_payload()

    command = parse_payment_refunded(consumed_message(value=to_json_bytes(payload)))

    assert command.order_id == UUID(str(payload["orderId"]))
    assert command.order_id != UUID(str(payload["aggregateId"]))
    assert command.event_id == UUID(str(payload["eventId"]))
    assert (command.amount, command.currency) == (Decimal("129.90"), "EUR")


def test_should_ignore_a_customer_in_the_payload() -> None:
    command = _parse(customerId="c1c2c3c4-0000-4000-8000-0000000000cc")

    assert not hasattr(command, "customer_id")


@pytest.mark.parametrize(
    "field",
    [
        "eventId",
        "aggregateId",
        "occurredAt",
        "version",
        "orderId",
        "amount",
        "currency",
        "refundedAt",
    ],
)
def test_should_reject_a_refund_without_a_required_field(field: str) -> None:
    assert f"{field}: Field required" in _reason(**{field: MISSING})


@pytest.mark.parametrize("amount", ["129.90", True, Decimal("-1")])
def test_should_reject_an_amount_that_is_not_a_non_negative_json_number(amount: object) -> None:
    assert "amount" in _reason(amount=amount)


def test_should_accept_an_integer_amount() -> None:
    command = _parse(amount=100)

    assert command.amount == Decimal(100)


def test_should_keep_a_large_amount_exact() -> None:
    command = _parse(amount=Decimal("123456789012345678.123456789"))

    assert command.amount == Decimal("123456789012345678.123456789")


@pytest.mark.parametrize("currency", ["eur", "EURO", 123])
def test_should_reject_a_currency_that_is_not_three_uppercase_letters(currency: object) -> None:
    assert "currency" in _reason(currency=currency)


def test_should_reject_a_refund_time_without_timezone() -> None:
    assert "refundedAt" in _reason(refundedAt="2026-11-16T10:05:00")


def test_should_reject_a_refund_that_is_not_json() -> None:
    with pytest.raises(InvalidMessageError, match="not valid JSON"):
        parse_payment_refunded(consumed_message(value=b"not json"))
