"""Use case: record the "order failure explanation" notification (``ai.order-triage-completed``).

The event carries no customer: like a refund, the customer is the one of the order context
recorded from ``orders.order-placed`` (ADR 0003). The result is advisory (ADR 0005): it is
stored and shown to the customer, and nothing acts on its suggested action.
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
from notifications.domain.triage import SuggestedAction, TriageDetails, TriageSource


@dataclass(frozen=True, slots=True)
class OrderTriageCompletedCommand:
    """The data of an ``ai.order-triage-completed`` event that this use case needs."""

    event_id: UUID
    order_id: UUID
    explanation: str
    """The explanation as received; the domain cleans it."""
    suggested_action: SuggestedAction
    source: TriageSource
    confidence: Decimal
    prompt_version: str


class RecordOrderFailureExplanationService:
    """Records the ``ORDER_FAILURE_EXPLANATION`` notification for the customer of the order."""

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

    async def handle(self, command: OrderTriageCompletedCommand) -> RecordOutcome:
        """Find the order's customer, then store and send the notification.

        Never writes an order context and never stores a notification without a customer.

        Raises:
            OrderContextNotFoundError: if no order context exists for ``command.order_id``
                (retryable: the consumer retries, then dead-letters the message).
            DomainError: if the explanation is empty once cleaned or the details are invalid.
            Exception: any repository failure, unchanged, so that the consumer can retry.
        """
        customer_id = await customer_of_order(self._order_contexts, command.order_id)
        notification = Notification.order_failure_explanation(
            notification_id=self._ids.new_id(),
            customer_id=customer_id,
            order_id=command.order_id,
            explanation=command.explanation,
            triage=TriageDetails.of(
                suggested_action=command.suggested_action,
                source=command.source,
                confidence=command.confidence,
                prompt_version=command.prompt_version,
            ),
            source_event_id=command.event_id,
            now=self._clock.now(),
        )
        return await record_and_send(notification, self._notifications, self._sender)
