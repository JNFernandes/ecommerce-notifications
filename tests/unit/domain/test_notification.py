"""Tests for the Notification aggregate."""

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.domain.errors import InvalidOrderTotalError, NaiveTimestampError
from notifications.domain.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)

NOTIFICATION_ID = UUID("00000000-0000-4000-8000-000000000001")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-0000000000aa")
EVENT_ID = UUID("6a1b2c3d-0000-4000-8000-000000000001")
NOW = datetime(2026, 11, 16, 10, 0, 0, 123_456, tzinfo=UTC)


def _order_received(**overrides: object) -> Notification:
    arguments: dict[str, object] = {
        "notification_id": NOTIFICATION_ID,
        "customer_id": CUSTOMER_ID,
        "order_id": ORDER_ID,
        "total_amount": Decimal("129.9"),
        "source_event_id": EVENT_ID,
        "now": NOW,
    }
    arguments.update(overrides)
    return Notification.order_received(**arguments)  # type: ignore[arg-type]  # test helper: overrides are typed per test


def test_should_build_an_order_received_email_notification_marked_sent() -> None:
    notification = _order_received()

    assert notification == Notification(
        id=NOTIFICATION_ID,
        customer_id=CUSTOMER_ID,
        order_id=ORDER_ID,
        type=NotificationType.ORDER_RECEIVED,
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.SENT,
        message=f"Your order {ORDER_ID} for 129.90 was received.",
        created_at=datetime(2026, 11, 16, 10, 0, 0, 123_000, tzinfo=UTC),
        source_event_id=EVENT_ID,
    )


def test_should_use_the_email_channel_value_expected_by_the_api() -> None:
    assert _order_received().channel == "email"


def test_should_reject_a_creation_time_without_timezone() -> None:
    with pytest.raises(NaiveTimestampError):
        _order_received(now=datetime(2026, 11, 16, 10, 0))


def test_should_reject_a_negative_total() -> None:
    with pytest.raises(InvalidOrderTotalError):
        _order_received(total_amount=Decimal("-1"))


def test_should_not_allow_changing_a_stored_notification() -> None:
    notification = _order_received()

    with pytest.raises(dataclasses.FrozenInstanceError):
        notification.message = "changed"  # type: ignore[misc]  # immutability is what is tested


def test_should_offer_one_type_per_order_event_with_the_stored_values() -> None:
    assert [member.value for member in NotificationType] == [
        "ORDER_RECEIVED",
        "ORDER_CONFIRMED",
        "ORDER_CANCELLED",
    ]


def test_should_build_an_order_confirmed_email_notification_marked_sent() -> None:
    notification = Notification.order_confirmed(
        notification_id=NOTIFICATION_ID,
        customer_id=CUSTOMER_ID,
        order_id=ORDER_ID,
        source_event_id=EVENT_ID,
        now=NOW,
    )

    assert notification == Notification(
        id=NOTIFICATION_ID,
        customer_id=CUSTOMER_ID,
        order_id=ORDER_ID,
        type=NotificationType.ORDER_CONFIRMED,
        channel=NotificationChannel.EMAIL,
        status=NotificationStatus.SENT,
        message=f"Your order {ORDER_ID} was confirmed.",
        created_at=datetime(2026, 11, 16, 10, 0, 0, 123_000, tzinfo=UTC),
        source_event_id=EVENT_ID,
    )


def test_should_reject_a_confirmation_time_without_timezone() -> None:
    with pytest.raises(NaiveTimestampError):
        Notification.order_confirmed(
            notification_id=NOTIFICATION_ID,
            customer_id=CUSTOMER_ID,
            order_id=ORDER_ID,
            source_event_id=EVENT_ID,
            now=datetime(2026, 11, 16, 10, 0),
        )


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        (
            "stock rejected: insufficient stock",
            f"Your order {ORDER_ID} was cancelled: stock rejected: insufficient stock.",
        ),
        (None, f"Your order {ORDER_ID} was cancelled."),
    ],
)
def test_should_build_an_order_cancelled_notification_with_or_without_a_reason(
    reason: str | None, message: str
) -> None:
    notification = Notification.order_cancelled(
        notification_id=NOTIFICATION_ID,
        customer_id=CUSTOMER_ID,
        order_id=ORDER_ID,
        reason=reason,
        source_event_id=EVENT_ID,
        now=NOW,
    )

    assert notification.type is NotificationType.ORDER_CANCELLED
    assert notification.message == message
    assert (notification.channel, notification.status) == (
        NotificationChannel.EMAIL,
        NotificationStatus.SENT,
    )
    assert notification.customer_id == CUSTOMER_ID
