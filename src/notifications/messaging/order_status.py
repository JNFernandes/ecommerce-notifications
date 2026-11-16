"""Parsers for ``orders.order-confirmed`` and ``orders.order-cancelled``.

Contract: ``ecommerce-infra/docs/events.md`` (Orders events). Both events are the shared order
envelope (``notifications.messaging.events``); each parser returns the application command.
"""

from notifications.application.record_order_status import OrderConfirmedCommand
from notifications.messaging.events import OrderEventEnvelope, parse_event
from notifications.messaging.messages import ConsumedMessage


class OrderConfirmedEvent(OrderEventEnvelope):
    """An ``orders.order-confirmed`` event: Orders confirmed a pending order (its payment was
    processed and its stock reserved, or an operator confirmed it)."""

    def to_command(self) -> OrderConfirmedCommand:
        """Map the event to the application command."""
        return OrderConfirmedCommand(
            event_id=self.event_id, order_id=self.aggregate_id, customer_id=self.customer_id
        )


def parse_order_confirmed(message: ConsumedMessage) -> OrderConfirmedCommand:
    """Decode and validate an ``orders.order-confirmed`` message into the application command.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract.
    """
    return parse_event(message, OrderConfirmedEvent).to_command()
