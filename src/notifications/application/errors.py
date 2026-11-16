"""Typed errors raised by the application services."""

from uuid import UUID


class OrderContextNotFoundError(Exception):
    """No order context exists yet for the order of an event that needs its customer.

    Retryable: the context is recorded from ``orders.order-placed`` and may arrive later, so the
    consumer retries with backoff and dead-letters the message only when the retries run out
    (constitution Principle IV, ADR 0003). It is deliberately not an ``InvalidMessageError``.
    """

    def __init__(self, order_id: UUID) -> None:
        """Create the error for ``order_id``."""
        super().__init__(f"no order context for order {order_id}")
        self.order_id = order_id


class NotificationNotFoundError(Exception):
    """No notification exists with the requested id (mapped to 404 by the API layer)."""

    def __init__(self, notification_id: UUID) -> None:
        """Create the error for ``notification_id``."""
        super().__init__(f"notification {notification_id} not found")
        self.notification_id = notification_id
