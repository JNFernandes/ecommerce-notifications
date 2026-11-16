"""Tests for parsing ``orders.order-confirmed`` and ``orders.order-cancelled`` messages."""

from uuid import UUID

import pytest

from notifications.messaging.messages import InvalidMessageError
from notifications.messaging.order_status import parse_order_cancelled, parse_order_confirmed
from tests.support.events import MISSING, order_status_payload, to_json_bytes
from tests.support.fakes import consumed_message


def _confirmed_reason(**overrides: object) -> str:
    message = consumed_message(value=to_json_bytes(order_status_payload(**overrides)))
    with pytest.raises(InvalidMessageError) as raised:
        parse_order_confirmed(message)
    return raised.value.reason


def test_should_parse_a_confirmation_into_the_command() -> None:
    payload = order_status_payload()

    command = parse_order_confirmed(consumed_message(value=to_json_bytes(payload)))

    assert command.event_id == UUID(str(payload["eventId"]))
    assert command.order_id == UUID(str(payload["aggregateId"]))
    assert command.customer_id == UUID(str(payload["customerId"]))


def test_should_ignore_a_reason_on_a_confirmation() -> None:
    payload = order_status_payload(reason="not used")

    command = parse_order_confirmed(consumed_message(value=to_json_bytes(payload)))

    assert not hasattr(command, "reason")


@pytest.mark.parametrize("field", ["eventId", "aggregateId", "occurredAt", "version", "customerId"])
def test_should_reject_a_confirmation_without_a_required_field(field: str) -> None:
    assert f"{field}: Field required" in _confirmed_reason(**{field: MISSING})


def test_should_reject_a_confirmation_with_an_invalid_uuid() -> None:
    assert "aggregateId" in _confirmed_reason(aggregateId="not-a-uuid")


def test_should_reject_a_confirmation_with_another_version() -> None:
    assert "version" in _confirmed_reason(version=2)


def test_should_reject_a_confirmation_that_is_not_json() -> None:
    with pytest.raises(InvalidMessageError, match="not valid JSON"):
        parse_order_confirmed(consumed_message(value=b"not json"))


def _cancel(**overrides: object) -> bytes:
    return to_json_bytes(order_status_payload(**overrides))


def test_should_parse_a_cancellation_and_pass_the_reason_on_unchanged() -> None:
    payload = order_status_payload(reason="  payment failed: card declined. ")

    command = parse_order_cancelled(consumed_message(value=to_json_bytes(payload)))

    assert command.event_id == UUID(str(payload["eventId"]))
    assert command.order_id == UUID(str(payload["aggregateId"]))
    assert command.customer_id == UUID(str(payload["customerId"]))
    assert command.reason == "  payment failed: card declined. "


@pytest.mark.parametrize("reason", [MISSING, None])
def test_should_read_an_absent_or_null_reason_as_no_reason(reason: object) -> None:
    command = parse_order_cancelled(consumed_message(value=_cancel(reason=reason)))

    assert command.reason is None


@pytest.mark.parametrize("reason", [42, True, {"text": "x"}, ["x"]])
def test_should_reject_a_reason_that_is_not_a_string_or_null(reason: object) -> None:
    with pytest.raises(InvalidMessageError) as raised:
        parse_order_cancelled(consumed_message(value=_cancel(reason=reason)))

    assert "reason: Value error, must be a JSON string or null" in raised.value.reason


def test_should_reject_a_cancellation_without_a_customer() -> None:
    with pytest.raises(InvalidMessageError, match="customerId: Field required"):
        parse_order_cancelled(consumed_message(value=_cancel(customerId=MISSING)))


def test_should_reject_a_cancellation_that_is_not_json() -> None:
    with pytest.raises(InvalidMessageError, match="not valid JSON"):
        parse_order_cancelled(consumed_message(value=b"not json"))
