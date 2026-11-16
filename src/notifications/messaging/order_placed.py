"""Consumer for ``orders.order-placed``: parse, validate and call one application service.

Contract: ``ecommerce-infra/docs/events.md`` (Orders events / ``orders.order-placed``). Payloads
are decoded with ``decode_json_object`` so amounts stay exact (research R1), then validated in
Python mode. The ``BeforeValidator`` guards reject values of the wrong JSON type that Pydantic's
lax mode would otherwise coerce (``true`` or ``1.0`` as ``version``, ``"129.90"`` as an amount).
"""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from notifications.application.record_order_received import (
    OrderPlacedCommand,
    RecordOrderReceivedService,
)
from notifications.messaging.messages import (
    ConsumedMessage,
    InvalidMessageError,
    decode_json_object,
)

ORDER_PLACED_TOPIC = "orders.order-placed"


def _json_string(value: object) -> object:
    if not isinstance(value, str):
        raise ValueError("must be a JSON string")
    return value


def _json_integer(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a JSON integer")
    return value


def _json_number(value: object) -> object:
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise ValueError("must be a JSON number")
    return Decimal(value)


type JsonUuid = Annotated[UUID, BeforeValidator(_json_string)]


class OrderPlacedEvent(BaseModel):
    """An ``orders.order-placed`` event, published by Orders after an order is saved."""

    model_config = ConfigDict(frozen=True, extra="ignore", use_attribute_docstrings=True)

    event_id: JsonUuid = Field(alias="eventId")
    """Unique id of this event; the idempotency key (stored as the notification's
    ``sourceEventId``)."""

    aggregate_id: JsonUuid = Field(alias="aggregateId")
    """Id of the order that was placed (for ``orders.*`` events the aggregate is the order)."""

    occurred_at: Annotated[AwareDatetime, BeforeValidator(_json_string)] = Field(alias="occurredAt")
    """When the order was placed, ISO 8601 with a timezone."""

    version: Annotated[Literal[1], BeforeValidator(_json_integer)]
    """Schema version of the payload; only version 1 exists."""

    customer_id: JsonUuid = Field(alias="customerId")
    """The customer who placed the order."""

    total_amount: Annotated[Decimal, BeforeValidator(_json_number), Field(ge=0)] = Field(
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
    payload = decode_json_object(message.value)
    try:
        return OrderPlacedEvent.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise InvalidMessageError(problems) from exc


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
