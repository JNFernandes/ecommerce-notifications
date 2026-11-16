"""Parser for ``orders.order-placed``: the event model and its mapping to the command.

Contract: ``ecommerce-infra/docs/events.md`` (Orders events / ``orders.order-placed``). The
envelope, the JSON-type guards and the parsing are shared with the other order events
(``notifications.messaging.events``).
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field

from notifications.application.record_order_received import OrderPlacedCommand
from notifications.messaging.events import OrderEventEnvelope, json_number, parse_event
from notifications.messaging.messages import ConsumedMessage


class OrderPlacedEvent(OrderEventEnvelope):
    """An ``orders.order-placed`` event, published by Orders after an order is saved."""

    total_amount: Annotated[Decimal, BeforeValidator(json_number), Field(ge=0)] = Field(
        alias="totalAmount"
    )
    """Sum of all item subtotals, as an exact decimal (no currency in this event)."""

    def to_command(self) -> OrderPlacedCommand:
        """Map the event to the application command."""
        return OrderPlacedCommand(
            event_id=self.event_id,
            order_id=self.aggregate_id,
            customer_id=self.customer_id,
            total_amount=self.total_amount,
        )


def parse_order_placed(message: ConsumedMessage) -> OrderPlacedCommand:
    """Decode and validate an ``orders.order-placed`` message into the application command.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract; the reason
            lists every invalid field as ``field: problem``.
    """
    return parse_event(message, OrderPlacedEvent).to_command()
