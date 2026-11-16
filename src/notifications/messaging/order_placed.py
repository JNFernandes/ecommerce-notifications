"""Consumer for ``orders.order-placed``: parse, validate and call one application service.

Contract: ``ecommerce-infra/docs/events.md`` (Orders events / ``orders.order-placed``). The
envelope, the JSON-type guards and the parsing are shared with the other order events
(``notifications.messaging.events``).
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field

from notifications.application.record_order_received import (
    OrderPlacedCommand,
    RecordOrderReceivedService,
)
from notifications.messaging.events import OrderEventEnvelope, json_number, parse_event
from notifications.messaging.messages import ConsumedMessage

ORDER_PLACED_TOPIC = "orders.order-placed"


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


def parse_order_placed(message: ConsumedMessage) -> OrderPlacedEvent:
    """Decode and validate an ``orders.order-placed`` message.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract; the reason
            lists every invalid field as ``field: problem``.
    """
    return parse_event(message, OrderPlacedEvent)


class OrderPlacedHandler:
    """Handles ``orders.order-placed``: one call to ``RecordOrderReceivedService.handle``."""

    topic: str = ORDER_PLACED_TOPIC

    def __init__(self, service: RecordOrderReceivedService) -> None:
        """Delegate valid events to ``service``."""
        self._service = service

    async def handle(self, message: ConsumedMessage) -> None:
        """Parse the message and record the notification.

        Raises:
            InvalidMessageError: for content that breaks the contract (never retried).
        """
        event = parse_order_placed(message)
        await self._service.handle(event.to_command())
