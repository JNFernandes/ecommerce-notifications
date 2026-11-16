"""Tests for recording the order confirmed and order cancelled notifications."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from notifications.application.record_order_status import (
    OrderCancelledCommand,
    OrderConfirmedCommand,
    RecordOrderStatusService,
)
from notifications.application.recording import RecordOutcome
from notifications.domain.notification import NotificationStatus, NotificationType
from tests.support.fakes import (
    FixedClock,
    InMemoryNotificationRepository,
    RecordingSender,
    SequentialIdGenerator,
)

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-000000000201")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
CONFIRMED = OrderConfirmedCommand(
    event_id=UUID("6a1b2c3d-0000-4000-8000-000000000201"),
    order_id=ORDER_ID,
    customer_id=CUSTOMER_ID,
)


class Fixture:
    """Service under test with in-memory collaborators."""

    def __init__(self, notifications: InMemoryNotificationRepository | None = None) -> None:
        self.notifications = notifications or InMemoryNotificationRepository()
        self.sender = RecordingSender()
        self.service = RecordOrderStatusService(
            self.notifications, self.sender, FixedClock(NOW), SequentialIdGenerator()
        )


async def test_should_store_and_send_the_order_confirmed_notification_once() -> None:
    fixture = Fixture()

    outcome = await fixture.service.record_confirmed(CONFIRMED)

    assert outcome is RecordOutcome.RECORDED
    stored = fixture.notifications.by_source_event[CONFIRMED.event_id]
    assert stored.type is NotificationType.ORDER_CONFIRMED
    assert stored.status is NotificationStatus.SENT
    assert (stored.customer_id, stored.order_id) == (CUSTOMER_ID, ORDER_ID)
    assert stored.message == f"Your order {ORDER_ID} was confirmed."
    assert fixture.sender.sent == [stored]


async def test_should_not_send_a_redelivered_confirmation_again() -> None:
    fixture = Fixture()
    await fixture.service.record_confirmed(CONFIRMED)

    outcome = await fixture.service.record_confirmed(CONFIRMED)

    assert outcome is RecordOutcome.ALREADY_RECORDED
    assert len(fixture.sender.sent) == 1


async def test_should_propagate_a_repository_failure_for_a_confirmation() -> None:
    fixture = Fixture(InMemoryNotificationRepository(error=ConnectionError("mongo down")))

    with pytest.raises(ConnectionError):
        await fixture.service.record_confirmed(CONFIRMED)

    assert fixture.sender.sent == []


def _cancelled(reason: str | None) -> OrderCancelledCommand:
    return OrderCancelledCommand(
        event_id=UUID("6a1b2c3d-0000-4000-8000-000000000202"),
        order_id=ORDER_ID,
        customer_id=CUSTOMER_ID,
        reason=reason,
    )


async def test_should_store_and_send_the_order_cancelled_notification_with_its_reason() -> None:
    fixture = Fixture()
    command = _cancelled("payment failed: amount exceeds the threshold")

    outcome = await fixture.service.record_cancelled(command)

    assert outcome is RecordOutcome.RECORDED
    stored = fixture.notifications.by_source_event[command.event_id]
    assert stored.type is NotificationType.ORDER_CANCELLED
    assert stored.customer_id == CUSTOMER_ID
    assert stored.message == (
        f"Your order {ORDER_ID} was cancelled: payment failed: amount exceeds the threshold."
    )
    assert fixture.sender.sent == [stored]


async def test_should_use_the_generic_text_for_a_cancellation_without_a_reason() -> None:
    fixture = Fixture()
    command = _cancelled(None)

    await fixture.service.record_cancelled(command)

    stored = fixture.notifications.by_source_event[command.event_id]
    assert stored.message == f"Your order {ORDER_ID} was cancelled."


async def test_should_not_send_a_redelivered_cancellation_again() -> None:
    fixture = Fixture()
    await fixture.service.record_cancelled(_cancelled(None))

    outcome = await fixture.service.record_cancelled(_cancelled(None))

    assert outcome is RecordOutcome.ALREADY_RECORDED
    assert len(fixture.sender.sent) == 1


async def test_should_propagate_a_repository_failure_for_a_cancellation() -> None:
    fixture = Fixture(InMemoryNotificationRepository(error=ConnectionError("mongo down")))

    with pytest.raises(ConnectionError):
        await fixture.service.record_cancelled(_cancelled("x"))

    assert fixture.sender.sent == []
