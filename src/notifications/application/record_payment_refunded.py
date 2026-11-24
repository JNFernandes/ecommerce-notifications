"""Use case: record the "payment refunded" notification (US-03).

``payments.payment-refunded`` carries no customer: the customer is the one of the order context
recorded from ``orders.order-placed`` (ADR 0003).
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from notifications.application.order_customer import customer_of_order
from notifications.application.ports import (
    Clock,
    IdGenerator,
    NotificationRepository,
    NotificationSender,
    OrderContextRepository,
)
from notifications.application.recording import RecordOutcome, record_and_send
from notifications.domain.notification import Notification


@dataclass(frozen=True, slots=True)
class PaymentRefundedCommand:
    """The data of a ``payments.payment-refunded`` event that this use case needs."""

    event_id: UUID
    order_id: UUID
    """The event's ``orderId`` (never ``aggregateId``, which is the payment id)."""
    amount: Decimal
    currency: str


class RecordPaymentRefundedService:
    """Records the ``PAYMENT_REFUNDED`` notification for the customer of the refunded order."""

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

    async def handle(self, command: PaymentRefundedCommand) -> RecordOutcome:
        """Find the order's customer, then store and send the notification.

        Never writes an order context and never stores a notification without a customer.

        Raises:
            OrderContextNotFoundError: if no order context exists for ``command.order_id``
                (retryable: the consumer retries, then dead-letters the message).
            Exception: any repository failure, unchanged, so that the consumer can retry.
        """
        customer_id = await customer_of_order(self._order_contexts, command.order_id)
        notification = Notification.payment_refunded(
            notification_id=self._ids.new_id(),
            customer_id=customer_id,
            order_id=command.order_id,
            amount=command.amount,
            currency=command.currency,
            source_event_id=command.event_id,
            now=self._clock.now(),
        )
        return await record_and_send(notification, self._notifications, self._sender)
