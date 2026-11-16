"""Tests for the shared store-then-send step."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.application.recording import RecordOutcome, record_and_send
from notifications.domain.notification import Notification
from tests.support.fakes import InMemoryNotificationRepository, RecordingSender

NOTIFICATION = Notification.order_received(
    notification_id=UUID(int=1),
    customer_id=UUID(int=2),
    order_id=UUID(int=3),
    total_amount=Decimal("5"),
    source_event_id=UUID(int=4),
    now=datetime(2026, 11, 16, tzinfo=UTC),
)


async def test_should_store_and_send_a_new_notification_once() -> None:
    notifications, sender = InMemoryNotificationRepository(), RecordingSender()

    outcome = await record_and_send(NOTIFICATION, notifications, sender)

    assert outcome is RecordOutcome.RECORDED
    assert notifications.by_source_event == {NOTIFICATION.source_event_id: NOTIFICATION}
    assert sender.sent == [NOTIFICATION]


async def test_should_not_send_a_duplicate_and_log_it_with_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notifications, sender = InMemoryNotificationRepository(), RecordingSender()
    await record_and_send(NOTIFICATION, notifications, sender)
    caplog.set_level(logging.INFO, logger="notifications.application")

    outcome = await record_and_send(NOTIFICATION, notifications, sender)

    assert outcome is RecordOutcome.ALREADY_RECORDED
    assert sender.sent == [NOTIFICATION]
    assert [record.getMessage() for record in caplog.records] == [
        f"Event already recorded: type=ORDER_RECEIVED sourceEvent={UUID(int=4)} order={UUID(int=3)}"
    ]


async def test_should_propagate_a_repository_failure_without_sending() -> None:
    sender = RecordingSender()

    with pytest.raises(ConnectionError):
        await record_and_send(
            NOTIFICATION, InMemoryNotificationRepository(error=ConnectionError("down")), sender
        )

    assert sender.sent == []
