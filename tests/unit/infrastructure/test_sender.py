"""Tests for the simulated email send."""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.domain.notification import Notification
from notifications.infrastructure.sender import LoggingNotificationSender


def test_should_log_one_info_line_with_the_notification_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    notification = Notification.order_received(
        notification_id=UUID(int=1),
        customer_id=UUID(int=2),
        order_id=UUID(int=3),
        total_amount=Decimal("5"),
        source_event_id=UUID(int=4),
        now=datetime(2026, 11, 16, tzinfo=UTC),
    )
    caplog.set_level(logging.INFO, logger="notifications.send")

    LoggingNotificationSender().send(notification)

    [record] = caplog.records
    assert record.name == "notifications.send"
    assert record.levelno == logging.INFO
    assert record.getMessage() == (
        f"Simulated email sent: notification={UUID(int=1)} type=ORDER_RECEIVED channel=email "
        f"customer={UUID(int=2)} order={UUID(int=3)} sourceEvent={UUID(int=4)}"
    )
