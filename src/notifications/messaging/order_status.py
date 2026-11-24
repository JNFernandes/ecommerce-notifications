"""Parsers for ``orders.order-confirmed`` and ``orders.order-cancelled``.

Contract: ``ecommerce-infra/docs/events.md`` (Orders events). Both events are the shared order
envelope (``notifications.messaging.events``); each parser returns the application command.
"""

from typing import Annotated

from pydantic import BeforeValidator

from notifications.application.record_order_status import (
    OrderCancelledCommand,
    OrderConfirmedCommand,
)
from notifications.messaging.events import OrderEventEnvelope, json_string_or_null, parse_event
from notifications.messaging.messages import ConsumedMessage


class OrderConfirmedEvent(OrderEventEnvelope):
    """An ``orders.order-confirmed`` event: Orders confirmed a pending order (its payment was
    processed and its stock reserved, or an operator confirmed it)."""

    def to_command(self) -> OrderConfirmedCommand:
        """Map the event to the application command."""
        return OrderConfirmedCommand(
            event_id=self.event_id, order_id=self.aggregate_id, customer_id=self.customer_id
        )


class OrderCancelledEvent(OrderEventEnvelope):
    """An ``orders.order-cancelled`` event: Orders cancelled a pending order."""

    reason: Annotated[str | None, BeforeValidator(json_string_or_null)] = None
    """Why the order was cancelled, when it came from an outcome (``payment failed: …`` or
    ``stock rejected: …``); absent for a cancellation requested through the Orders API."""

    def to_command(self) -> OrderCancelledCommand:
        """Map the event to the application command (the reason is passed on unchanged)."""
        return OrderCancelledCommand(
            event_id=self.event_id,
            order_id=self.aggregate_id,
            customer_id=self.customer_id,
            reason=self.reason,
        )


def parse_order_confirmed(message: ConsumedMessage) -> OrderConfirmedCommand:
    """Decode and validate an ``orders.order-confirmed`` message into the application command.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract.
    """
    return parse_event(message, OrderConfirmedEvent).to_command()


def parse_order_cancelled(message: ConsumedMessage) -> OrderCancelledCommand:
    """Decode and validate an ``orders.order-cancelled`` message into the application command.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract, including
            a ``reason`` that is neither a JSON string nor ``null``.
    """
    return parse_event(message, OrderCancelledEvent).to_command()
