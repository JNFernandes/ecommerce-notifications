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
    order_failure_explanation_message,
    order_received_message,
    payment_refunded_message,
)
from notifications.domain.time import require_utc_millis
from notifications.domain.triage import TriageDetails


class NotificationType(StrEnum):
    """What the notification is about."""

    ORDER_RECEIVED = "ORDER_RECEIVED"
    """From ``orders.order-placed`` (US-01)."""
    ORDER_CONFIRMED = "ORDER_CONFIRMED"
    """From ``orders.order-confirmed`` (US-02)."""
    ORDER_CANCELLED = "ORDER_CANCELLED"
    """From ``orders.order-cancelled`` (US-02)."""
    PAYMENT_REFUNDED = "PAYMENT_REFUNDED"
    """From ``payments.payment-refunded`` (US-03)."""
    ORDER_FAILURE_EXPLANATION = "ORDER_FAILURE_EXPLANATION"
    """From ``ai.order-triage-completed``: why the order failed, advisory (ADR 0005)."""


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
    triage: TriageDetails | None = None
    """The AI triage details; only on ``ORDER_FAILURE_EXPLANATION`` notifications."""

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
    def payment_refunded(
        cls,
        *,
        notification_id: UUID,
        customer_id: UUID,
        order_id: UUID,
        amount: Decimal,
        currency: str,
        source_event_id: UUID,
        now: datetime,
    ) -> Self:
        """Build the ``PAYMENT_REFUNDED`` notification for the customer of the order.

        Raises:
            InvalidAmountError: if ``amount`` is negative or not finite.
            InvalidCurrencyError: if ``currency`` is not a three-letter uppercase code.
            NaiveTimestampError: if ``now`` has no timezone.
        """
        return cls._create(
            notification_id=notification_id,
            customer_id=customer_id,
            order_id=order_id,
            notification_type=NotificationType.PAYMENT_REFUNDED,
            message=payment_refunded_message(order_id, amount, currency),
            source_event_id=source_event_id,
            now=now,
        )

    @classmethod
    def order_failure_explanation(
        cls,
        *,
        notification_id: UUID,
        customer_id: UUID,
        order_id: UUID,
        explanation: str,
        triage: TriageDetails,
        source_event_id: UUID,
        now: datetime,
    ) -> Self:
        """Build the ``ORDER_FAILURE_EXPLANATION`` notification from an AI triage result.

        The message is the explanation, cleaned as untrusted plain text. The notification is
        advisory: it is stored and shown, and nothing acts on ``triage.suggested_action``.

        Raises:
            InvalidExplanationError: if nothing is left of the explanation once cleaned.
            NaiveTimestampError: if ``now`` has no timezone.
        """
        return cls._create(
            notification_id=notification_id,
            customer_id=customer_id,
            order_id=order_id,
            notification_type=NotificationType.ORDER_FAILURE_EXPLANATION,
            message=order_failure_explanation_message(explanation),
            source_event_id=source_event_id,
            now=now,
            triage=triage,
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
        triage: TriageDetails | None = None,
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
            triage=triage,
        )
