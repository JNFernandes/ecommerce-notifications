"""Shared validation for inbound Orders events: JSON-type guards, the envelope, and parsing.

Contract: ``ecommerce-infra/docs/events.md`` (Conventions and Orders events). Payloads are decoded
with ``decode_json_object`` so amounts stay exact, then validated in Python mode. The
``BeforeValidator`` guards reject values of the wrong JSON type that Pydantic's lax mode would
otherwise coerce (``true`` or ``1.0`` as ``version``, ``"129.90"`` as an amount).
"""

from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

from notifications.messaging.messages import (
    ConsumedMessage,
    InvalidMessageError,
    decode_json_object,
)


def json_string(value: object) -> object:
    """Accept only a JSON string."""
    if not isinstance(value, str):
        raise ValueError("must be a JSON string")
    return value


def json_integer(value: object) -> object:
    """Accept only a JSON integer (not a boolean, not a decimal)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be a JSON integer")
    return value


def json_number(value: object) -> object:
    """Accept only a JSON number (integer or exact decimal), returned as ``Decimal``."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        raise ValueError("must be a JSON number")
    return Decimal(value)


type JsonUuid = Annotated[UUID, BeforeValidator(json_string)]


class EventEnvelope(BaseModel):
    """Fields every event carries (``ecommerce-infra/docs/events.md``, Conventions)."""

    model_config = ConfigDict(frozen=True, extra="ignore", use_attribute_docstrings=True)

    event_id: JsonUuid = Field(alias="eventId")
    """Unique id of this event; the idempotency key (stored as the notification's
    ``sourceEventId``)."""

    aggregate_id: JsonUuid = Field(alias="aggregateId")
    """Id of the aggregate that raised the event: the order for ``orders.*`` events, the payment
    for ``payments.*`` events (never the order; those carry ``orderId``)."""

    occurred_at: Annotated[AwareDatetime, BeforeValidator(json_string)] = Field(alias="occurredAt")
    """When it happened, ISO 8601 with a timezone."""

    version: Annotated[Literal[1], BeforeValidator(json_integer)]
    """Schema version of the payload; only version 1 exists."""


class OrderEventEnvelope(EventEnvelope):
    """Fields shared by every ``orders.*`` event: the envelope plus the customer."""

    customer_id: JsonUuid = Field(alias="customerId")
    """The customer of the order."""


def parse_event[E: BaseModel](message: ConsumedMessage, model: type[E]) -> E:
    """Decode ``message`` and validate it as ``model``.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract; the reason
            lists every invalid field as ``field: problem``.
    """
    payload = decode_json_object(message.value)
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise InvalidMessageError(problems) from exc
