"""The ``Notification`` aggregate: a record that a customer was told about an order event."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from notifications.domain.messages import (
    order_cancelled_message,
    order_confirmed_message,
    order_received_message,
)
from notifications.domain.time import require_utc_millis


class NotificationType(StrEnum):
    """What the notification is about."""

    ORDER_RECEIVED = "ORDER_RECEIVED"
    """From ``orders.order-placed`` (US-01)."""
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    """From ``orders.order-confirmed`` (US-02)."""
    ORDER_CANCELLED = "ORDER_CANCELLED"
    """From ``orders.order-cancelled`` (US-02)."""


class NotificationChannel(StrEnum):
    """How the notification reaches the customer."""

    EMAIL = "email"


class NotificationStatus(StrEnum):
    """Delivery status. Sending is simulated, so a stored notification is ``SENT``."""

    SENT = "SENT"


@dataclass(frozen=True, slots=True)
class Notification:
    """An immutable notification for one customer about one order event.

    At most one notification exists per ``source_event_id`` (enforced by a unique index).
    """

    id: UUID
    customer_id: UUID
    order_id: UUID
    type: NotificationType
    channel: NotificationChannel
    status: NotificationStatus
    message: str
    created_at: datetime
    source_event_id: UUID

    @classmethod
    def order_received(
        cls,
        *,
        notification_id: UUID,
        customer_id: UUID,
        order_id: UUID,
        total_amount: Decimal,
        source_event_id: UUID,
        now: datetime,
    ) -> Self:
        """Build the ``ORDER_RECEIVED`` notification for a placed order.

        Raises:
            InvalidOrderTotalError: if ``total_amount`` is negative or not finite.
            NaiveTimestampError: if ``now`` has no timezone.
        """
        return cls._create(
            notification_id=notification_id,
            customer_id=customer_id,
            order_id=order_id,
            notification_type=NotificationType.ORDER_RECEIVED,
            message=order_received_message(order_id, total_amount),
            source_event_id=source_event_id,
            now=now,
        )

    @classmethod
    def order_confirmed(
        cls,
        *,
        notification_id: UUID,
        customer_id: UUID,
        order_id: UUID,
        source_event_id: UUID,
        now: datetime,
    ) -> Self:
        """Build the ``ORDER_CONFIRMED`` notification for a confirmed order.

        Raises:
            NaiveTimestampError: if ``now`` has no timezone.
        """
        return cls._create(
            notification_id=notification_id,
            customer_id=customer_id,
            order_id=order_id,
            notification_type=NotificationType.ORDER_CONFIRMED,
            message=order_confirmed_message(order_id),
            source_event_id=source_event_id,
            now=now,
        )

    @classmethod
    def order_cancelled(
        cls,
        *,
        notification_id: UUID,
        customer_id: UUID,
        order_id: UUID,
        reason: str | None,
        source_event_id: UUID,
        now: datetime,
    ) -> Self:
        """Build the ``ORDER_CANCELLED`` notification, showing the reason when there is one.

        Raises:
            NaiveTimestampError: if ``now`` has no timezone.
        """
        return cls._create(
            notification_id=notification_id,
            customer_id=customer_id,
            order_id=order_id,
            notification_type=NotificationType.ORDER_CANCELLED,
            message=order_cancelled_message(order_id, reason),
            source_event_id=source_event_id,
            now=now,
        )

    @classmethod
    def _create(
        cls,
        *,
        notification_id: UUID,
        customer_id: UUID,
        order_id: UUID,
        notification_type: NotificationType,
        message: str,
        source_event_id: UUID,
        now: datetime,
    ) -> Self:
        """Apply the rules shared by every notification: email channel, ``SENT``, UTC millis."""
        return cls(
            id=notification_id,
            customer_id=customer_id,
            order_id=order_id,
            type=notification_type,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.SENT,
            message=message,
            created_at=require_utc_millis(now),
            source_event_id=source_event_id,
        )
