"""Tests for the shared order event envelope and parsing."""

from decimal import Decimal
from uuid import UUID

import pytest

from notifications.messaging.events import EventEnvelope, OrderEventEnvelope, parse_event
from notifications.messaging.messages import InvalidMessageError
from tests.support.events import MISSING, order_status_payload, to_json_bytes
from tests.support.fakes import consumed_message


def _reason(**overrides: object) -> str:
    message = consumed_message(value=to_json_bytes(order_status_payload(**overrides)))
    with pytest.raises(InvalidMessageError) as raised:
        parse_event(message, OrderEventEnvelope)
    return raised.value.reason


def test_should_parse_the_envelope_and_ignore_unknown_fields() -> None:
    payload = order_status_payload(somethingNew={"nested": True})

    envelope = parse_event(consumed_message(value=to_json_bytes(payload)), OrderEventEnvelope)

    assert envelope.event_id == UUID(str(payload["eventId"]))
    assert envelope.aggregate_id == UUID(str(payload["aggregateId"]))
    assert envelope.customer_id == UUID(str(payload["customerId"]))
    assert envelope.version == 1
    assert envelope.occurred_at.utcoffset() is not None


@pytest.mark.parametrize("version", [True, Decimal("1.0"), "1", 2])
def test_should_reject_any_version_other_than_the_json_integer_one(version: object) -> None:
    assert "version" in _reason(version=version)


def test_should_reject_a_missing_customer() -> None:
    assert "customerId: Field required" in _reason(customerId=MISSING)


def test_should_reject_a_uuid_given_as_a_number() -> None:
    assert "eventId: Value error, must be a JSON string" in _reason(eventId=12345)


def test_should_report_every_invalid_field() -> None:
    reason = _reason(customerId=MISSING, aggregateId="not-a-uuid")

    assert "customerId" in reason
    assert "aggregateId" in reason


def test_should_reject_a_value_that_is_not_json() -> None:
    with pytest.raises(InvalidMessageError, match="not valid JSON"):
        parse_event(consumed_message(value=b"not json"), OrderEventEnvelope)


def test_should_accept_an_event_without_customer_as_a_generic_envelope() -> None:
    payload = order_status_payload(customerId=MISSING)

    envelope = parse_event(consumed_message(value=to_json_bytes(payload)), EventEnvelope)

    assert envelope.event_id == UUID(str(payload["eventId"]))
    assert not hasattr(envelope, "customer_id")


def test_should_require_the_customer_only_for_order_events() -> None:
    message = consumed_message(value=to_json_bytes(order_status_payload(customerId=MISSING)))

    with pytest.raises(InvalidMessageError, match="customerId: Field required"):
        parse_event(message, OrderEventEnvelope)
