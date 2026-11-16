"""Use case: record the "order received" notification for a placed order (US-01)."""

import logging
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from notifications.application.ports import (
    Clock,
    IdGenerator,
    NotificationRepository,
    NotificationSender,
    OrderContextRepository,
)
from notifications.application.recording import RecordOutcome, record_and_send
from notifications.domain.notification import Notification
from notifications.domain.order_context import OrderContext

__all__ = ["OrderPlacedCommand", "RecordOrderReceivedService", "RecordOutcome"]

logger = logging.getLogger("notifications.application")


@dataclass(frozen=True, slots=True)
class OrderPlacedCommand:
    """The data of an ``orders.order-placed`` event that this use case needs."""

    event_id: UUID
    order_id: UUID
    customer_id: UUID
    total_amount: Decimal


class RecordOrderReceivedService:
    """Records the order context and the ``ORDER_RECEIVED`` notification, then sends it."""

    def __init__(
        self,
        order_contexts: OrderContextRepository,
        notifications: NotificationRepository,
        sender: NotificationSender,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the service from its ports."""
        self._order_contexts = order_contexts
        self._notifications = notifications
        self._sender = sender
        self._clock = clock
        self._ids = ids

    async def handle(self, command: OrderPlacedCommand) -> RecordOutcome:
        """Store the order context, then the notification, then send it.

        The context is written first so that a redelivery after a crash completes a partial
        result (FR-007). Both writes are idempotent on their own (ADR 0002), so a redelivered
        event changes nothing.

        Raises:
            InvalidOrderTotalError: if the total is negative or not finite.
            Exception: any repository failure, unchanged, so that the consumer can retry.
        """
        now = self._clock.now()
        context = OrderContext.from_order_placed(
            order_id=command.order_id,
            customer_id=command.customer_id,
            total_amount=command.total_amount,
            now=now,
        )
        existing = await self._order_contexts.add_if_absent(context)
        if existing is not None and existing.conflicts_with(context):
            logger.warning(
                "Order context already recorded with different values; keeping the first: "
                "order=%s existingCustomer=%s eventCustomer=%s existingTotal=%s eventTotal=%s "
                "sourceEvent=%s",
                command.order_id,
                existing.customer_id,
                command.customer_id,
                existing.total_amount,
                command.total_amount,
                command.event_id,
            )
        notification = Notification.order_received(
            notification_id=self._ids.new_id(),
            customer_id=command.customer_id,
            order_id=command.order_id,
            total_amount=command.total_amount,
            source_event_id=command.event_id,
            now=now,
        )
        return await record_and_send(notification, self._notifications, self._sender)
