"""Use cases: record the "order confirmed" and "order cancelled" notifications (US-02).

The customer comes from the event itself; these use cases never read or write the order
context (spec FR-009). Each method builds the notification through the domain and hands it to
the shared store-then-send step; there are no business rules here.
"""

from dataclasses import dataclass
from uuid import UUID

from notifications.application.ports import (
    Clock,
    IdGenerator,
    NotificationRepository,
    NotificationSender,
)
from notifications.application.recording import RecordOutcome, record_and_send
from notifications.domain.notification import Notification


@dataclass(frozen=True, slots=True)
class OrderConfirmedCommand:
    """The data of an ``orders.order-confirmed`` event that this use case needs."""

    event_id: UUID
    order_id: UUID
    customer_id: UUID


class RecordOrderStatusService:
    """Records notifications for changes of an order's status (confirmed, cancelled)."""

    def __init__(
        self,
        notifications: NotificationRepository,
        sender: NotificationSender,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        """Create the service from its ports."""
        self._notifications = notifications
        self._sender = sender
        self._clock = clock
        self._ids = ids

    async def record_confirmed(self, command: OrderConfirmedCommand) -> RecordOutcome:
        """Store and send the ``ORDER_CONFIRMED`` notification; a redelivery changes nothing.

        Raises:
            Exception: any repository failure, unchanged, so that the consumer can retry.
        """
        notification = Notification.order_confirmed(
            notification_id=self._ids.new_id(),
            customer_id=command.customer_id,
            order_id=command.order_id,
            source_event_id=command.event_id,
            now=self._clock.now(),
        )
        return await record_and_send(notification, self._notifications, self._sender)
