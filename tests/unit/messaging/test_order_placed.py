"""Tests for parsing ``orders.order-placed`` messages and routing them to the service."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.application.record_order_received import RecordOrderReceivedService
from notifications.messaging.messages import InvalidMessageError
from notifications.messaging.order_placed import parse_order_placed
from notifications.messaging.routing import Route
from tests.support.events import MISSING, order_placed_payload, to_json_bytes
from tests.support.fakes import (
    FixedClock,
    InMemoryNotificationRepository,
    InMemoryOrderContextRepository,
    RecordingSender,
    SequentialIdGenerator,
    consumed_message,
)


def _parse(**overrides: object) -> object:
    return parse_order_placed(
        consumed_message(value=to_json_bytes(order_placed_payload(**overrides)))
    )


def _reason(**overrides: object) -> str:
    with pytest.raises(InvalidMessageError) as raised:
        _parse(**overrides)
    return raised.value.reason


def test_should_parse_a_valid_event_and_map_it_to_the_command() -> None:
    payload = order_placed_payload(totalAmount=Decimal("129.9"))

    command = parse_order_placed(consumed_message(value=to_json_bytes(payload)))

    assert command.event_id == UUID(str(payload["eventId"]))
    assert command.order_id == UUID(str(payload["aggregateId"]))
    assert command.customer_id == UUID(str(payload["customerId"]))
    assert command.total_amount == Decimal("129.9")


def test_should_ignore_items_and_unknown_fields() -> None:
    _parse(items=MISSING, somethingNew={"nested": True})


def test_should_accept_an_integer_total() -> None:
    command = parse_order_placed(
        consumed_message(value=to_json_bytes(order_placed_payload(totalAmount=100)))
    )

    assert command.total_amount == Decimal(100)


def test_should_keep_a_large_total_exact() -> None:
    command = parse_order_placed(
        consumed_message(
            value=to_json_bytes(
                order_placed_payload(totalAmount=Decimal("123456789012345678.123456789"))
            )
        )
    )

    assert command.total_amount == Decimal("123456789012345678.123456789")


def test_should_accept_a_total_of_zero() -> None:
    _parse(totalAmount=Decimal("0"))


@pytest.mark.parametrize(
    "field", ["eventId", "aggregateId", "occurredAt", "version", "customerId", "totalAmount"]
)
def test_should_reject_an_event_without_a_required_field(field: str) -> None:
    assert f"{field}: Field required" in _reason(**{field: MISSING})


@pytest.mark.parametrize("field", ["eventId", "aggregateId", "customerId"])
def test_should_reject_an_invalid_uuid(field: str) -> None:
    assert field in _reason(**{field: "not-a-uuid"})


@pytest.mark.parametrize("field", ["eventId", "aggregateId", "customerId"])
def test_should_reject_a_uuid_that_is_not_a_json_string(field: str) -> None:
    assert f"{field}: Value error, must be a JSON string" in _reason(**{field: 12345})


@pytest.mark.parametrize("version", [2, 0, True, Decimal("1.0"), "1"])
def test_should_reject_any_version_other_than_the_integer_one(version: object) -> None:
    assert "version" in _reason(version=version)


@pytest.mark.parametrize("amount", ["129.90", True, None, [1]])
def test_should_reject_a_total_that_is_not_a_json_number(amount: object) -> None:
    assert "totalAmount: Value error, must be a JSON number" in _reason(totalAmount=amount)


def test_should_reject_a_negative_total() -> None:
    assert "totalAmount" in _reason(totalAmount=Decimal("-0.01"))


def test_should_reject_a_total_that_is_not_finite() -> None:
    message = consumed_message(
        value=to_json_bytes(order_placed_payload(totalAmount=MISSING))[:-1]
        + b', "totalAmount": NaN}'
    )

    with pytest.raises(InvalidMessageError, match="totalAmount"):
        parse_order_placed(message)


@pytest.mark.parametrize("occurred_at", ["2026-11-16T10:00:00", 1763287200])
def test_should_reject_an_occurrence_time_without_timezone_or_not_a_string(
    occurred_at: object,
) -> None:
    assert "occurredAt" in _reason(occurredAt=occurred_at)


def test_should_report_every_invalid_field_in_the_reason() -> None:
    reason = _reason(customerId=MISSING, version=2)

    assert "customerId" in reason
    assert "version" in reason


def test_should_reject_a_value_that_is_not_json() -> None:
    with pytest.raises(InvalidMessageError, match="not valid JSON"):
        parse_order_placed(consumed_message(value=b"not json"))


async def test_should_route_a_valid_event_to_the_service_once() -> None:
    notifications = InMemoryNotificationRepository()
    sender = RecordingSender()
    service = RecordOrderReceivedService(
        InMemoryOrderContextRepository(),
        notifications,
        sender,
        FixedClock(datetime(2026, 11, 16, tzinfo=UTC)),
        SequentialIdGenerator(),
    )
    payload = order_placed_payload()
    route = Route("orders.order-placed", parse_order_placed, service.handle)

    await route.run(consumed_message(value=to_json_bytes(payload)))

    [stored] = notifications.by_source_event.values()
    assert stored.source_event_id == UUID(str(payload["eventId"]))
    assert stored.order_id == UUID(str(payload["aggregateId"]))
    assert sender.sent == [stored]


async def test_should_not_call_the_service_for_an_invalid_message() -> None:
    notifications = InMemoryNotificationRepository()
    service = RecordOrderReceivedService(
        InMemoryOrderContextRepository(),
        notifications,
        RecordingSender(),
        FixedClock(datetime(2026, 11, 16, tzinfo=UTC)),
        SequentialIdGenerator(),
    )

    with pytest.raises(InvalidMessageError):
        await Route("orders.order-placed", parse_order_placed, service.handle).run(
            consumed_message(value=b"{}")
        )

    assert notifications.by_source_event == {}
