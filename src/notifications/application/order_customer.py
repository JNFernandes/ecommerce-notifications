"""The customer of an order, for events that only carry the order id (ADR 0003)."""

from uuid import UUID

from notifications.application.errors import OrderContextNotFoundError
from notifications.application.ports import OrderContextRepository


async def customer_of_order(order_contexts: OrderContextRepository, order_id: UUID) -> UUID:
    """Return the customer of ``order_id`` from the order context recorded at its placement.

    Never writes an order context.

    Raises:
        OrderContextNotFoundError: if no context exists yet for the order (retryable: the
            consumer retries, then dead-letters the message).
        Exception: any repository failure, unchanged, so that the consumer can retry.
    """
    context = await order_contexts.get(order_id)
    if context is None:
        raise OrderContextNotFoundError(order_id)
    return context.customer_id
