"""AI order triage results shown to the customer (``ai.order-triage-completed``, ADR 0005).

Advisory only: the domain stores and displays a triage result. Nothing acts on the suggested
action; it is advice for a person, kept as information on the notification.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Self

from notifications.domain.errors import InvalidTriageResultError
from notifications.domain.messages import plain_text

MAX_PROMPT_VERSION_LENGTH = 64
"""Longest prompt version kept on a notification; longer values are cut and end with ``…``."""


class SuggestedAction(StrEnum):
    """What the triage suggests a person does. Advice only: never carried out by any service."""

    CONTACT_CUSTOMER = "CONTACT_CUSTOMER"
    RESTOCK = "RESTOCK"
    FIX_CATALOG = "FIX_CATALOG"
    REVIEW = "REVIEW"
    NONE = "NONE"


class TriageSource(StrEnum):
    """What produced the triage result."""

    LLM = "LLM"
    """A language model."""
    FALLBACK_TEMPLATE = "FALLBACK_TEMPLATE"
    """The deterministic template of the triage worker (no model was used)."""


@dataclass(frozen=True, slots=True)
class TriageDetails:
    """The extra information of an ``ORDER_FAILURE_EXPLANATION`` notification."""

    suggested_action: SuggestedAction
    source: TriageSource
    confidence: Decimal
    """From 0 to 1."""
    prompt_version: str
    """Plain text, one line, at most ``MAX_PROMPT_VERSION_LENGTH`` characters."""

    @classmethod
    def of(
        cls,
        *,
        suggested_action: SuggestedAction,
        source: TriageSource,
        confidence: Decimal,
        prompt_version: str,
    ) -> Self:
        """Build the details, cleaning the prompt version like any untrusted text.

        Raises:
            InvalidTriageResultError: if ``confidence`` is not a finite number from 0 to 1, or the
                prompt version is empty once cleaned.
        """
        if not confidence.is_finite() or not Decimal(0) <= confidence <= Decimal(1):
            raise InvalidTriageResultError(f"confidence must be from 0 to 1, got {confidence}")
        shown_version = plain_text(prompt_version, MAX_PROMPT_VERSION_LENGTH)
        if not shown_version:
            raise InvalidTriageResultError("prompt version is empty")
        return cls(
            suggested_action=suggested_action,
            source=source,
            confidence=confidence,
            prompt_version=shown_version,
        )
