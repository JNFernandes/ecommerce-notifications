"""Parser for ``ai.order-triage-completed``.

Contract: ``ecommerce-infra/docs/events.md`` (AI events), published by the triage worker of
ecommerce-ai: ``aggregateId`` is the order id and equals ``orderId``; ``confidence`` is a JSON
number; ``modelId`` is a string or ``null``. The parser only validates; the domain cleans the
explanation (ADR 0005).
"""

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BeforeValidator, Field, model_validator

from notifications.application.record_order_failure_explanation import (
    OrderTriageCompletedCommand,
)
from notifications.domain.triage import SuggestedAction, TriageSource
from notifications.messaging.events import (
    EventEnvelope,
    JsonUuid,
    json_number,
    json_string,
    json_string_or_null,
    parse_event,
)
from notifications.messaging.messages import ConsumedMessage

type TriageCause = Literal[
    "PAYMENT_FAILED", "INSUFFICIENT_STOCK", "UNKNOWN_PRODUCT", "CANCELLATION_REQUESTED", "UNKNOWN"
]


class OrderTriageCompletedEvent(EventEnvelope):
    """An ``ai.order-triage-completed`` event: the AI triage explained why an order failed.

    Advisory only: it is information for people, never a command.
    """

    order_id: JsonUuid = Field(alias="orderId")
    """The order; equal to ``aggregateId``."""

    cause: Annotated[TriageCause, BeforeValidator(json_string)]
    """Why the order failed, as one of the contract's causes (validated, not stored)."""

    explanation: Annotated[str, BeforeValidator(json_string), Field(min_length=1, max_length=1000)]
    """Plain-text explanation, 1 to 1000 characters; untrusted, cleaned by the domain."""

    suggested_action: Annotated[SuggestedAction, BeforeValidator(json_string)] = Field(
        alias="suggestedAction"
    )
    """Advice for a person; never carried out."""

    confidence: Annotated[Decimal, BeforeValidator(json_number), Field(ge=0, le=1)]
    """How sure the triage is, from 0 to 1."""

    prompt_version: Annotated[str, BeforeValidator(json_string), Field(min_length=1)] = Field(
        alias="promptVersion"
    )
    """Version of the prompt that produced the result, for example ``triage.v1``."""

    model_id: Annotated[str | None, BeforeValidator(json_string_or_null)] = Field(alias="modelId")
    """The model id when ``source`` is ``LLM``, ``null`` otherwise (validated, not stored)."""

    source: Annotated[TriageSource, BeforeValidator(json_string)]
    """``LLM`` or ``FALLBACK_TEMPLATE``."""

    @model_validator(mode="after")
    def _order_is_the_aggregate(self) -> Self:
        """Reject an ``orderId`` that differs from ``aggregateId`` (both are the order)."""
        if self.order_id != self.aggregate_id:
            raise ValueError("orderId must equal aggregateId")
        return self

    def to_command(self) -> OrderTriageCompletedCommand:
        """Map the event to the application command (the explanation is passed on unchanged)."""
        return OrderTriageCompletedCommand(
            event_id=self.event_id,
            order_id=self.order_id,
            explanation=self.explanation,
            suggested_action=self.suggested_action,
            source=self.source,
            confidence=self.confidence,
            prompt_version=self.prompt_version,
        )


def parse_order_triage_completed(message: ConsumedMessage) -> OrderTriageCompletedCommand:
    """Decode and validate an ``ai.order-triage-completed`` message into the application command.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract, including
            an unknown ``version``, ``cause``, ``suggestedAction`` or ``source``.
    """
    return parse_event(message, OrderTriageCompletedEvent).to_command()
