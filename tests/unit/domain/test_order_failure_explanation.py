"""Tests for the order failure explanation: cleaning untrusted text, triage details, the factory."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.domain.errors import (
    InvalidExplanationError,
    InvalidTriageResultError,
    NaiveTimestampError,
)
from notifications.domain.messages import (
    MAX_EXPLANATION_LENGTH,
    order_failure_explanation_message,
    plain_text,
)
from notifications.domain.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)
from notifications.domain.triage import (
    MAX_PROMPT_VERSION_LENGTH,
    SuggestedAction,
    TriageDetails,
    TriageSource,
)

ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-0000000000ab")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
EVENT_ID = UUID("6a1b2c3d-0000-4000-8000-0000000000ab")
NOW = datetime(2026, 11, 24, 10, 0, 0, 123_456, tzinfo=UTC)
TRIAGE = TriageDetails.of(
    suggested_action=SuggestedAction.RESTOCK,
    source=TriageSource.LLM,
    confidence=Decimal("0.92"),
    prompt_version="triage.v1",
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Stock was short.  ", "Stock was short."),
        ("line one\r\n\r\nline two\tend", "line one line two end"),
        ("nul\x00 bell\x07 esc\x1b[31m red", "nul bell esc[31m red"),
        ("c1\x85next \x9bcsi", "c1 next csi"),
        ("bidi \u202eoverride\u202c zero\u200bwidth", "bidi override zerowidth"),
        ("sep\u2028line\u2029para", "sep line para"),
        ("lone \ud800surrogate", "lone surrogate"),
        ("<b>bold</b> & <script>x</script>", "<b>bold</b> & <script>x</script>"),
        ("   \t\n\x00 ", ""),
    ],
)
def test_should_clean_untrusted_text_into_one_line_of_plain_text(raw: str, expected: str) -> None:
    assert plain_text(raw, 500) == expected


def test_should_cut_over_long_text_to_the_limit_ending_with_an_ellipsis() -> None:
    shown = plain_text("a" * 600, 500)

    assert len(shown) == 500
    assert shown == "a" * 499 + "…"


def test_should_keep_text_exactly_at_the_limit_unchanged() -> None:
    assert plain_text("b" * 500, 500) == "b" * 500


def test_should_use_the_cleaned_explanation_as_the_message() -> None:
    message = order_failure_explanation_message(" Payment\nfailed.\x00 " + "x" * 1000)

    assert message.startswith("Payment failed. x")
    assert len(message) == MAX_EXPLANATION_LENGTH
    assert message.endswith("…")


@pytest.mark.parametrize("explanation", ["", "   ", "\x00\x01\u200b\n"])
def test_should_reject_an_explanation_that_is_empty_once_cleaned(explanation: str) -> None:
    with pytest.raises(InvalidExplanationError):
        order_failure_explanation_message(explanation)


def test_should_build_triage_details_cleaning_the_prompt_version() -> None:
    details = TriageDetails.of(
        suggested_action=SuggestedAction.NONE,
        source=TriageSource.FALLBACK_TEMPLATE,
        confidence=Decimal(1),
        prompt_version=" triage.v2\n" + "v" * 100,
    )

    assert details.suggested_action is SuggestedAction.NONE
    assert details.source is TriageSource.FALLBACK_TEMPLATE
    assert details.confidence == Decimal(1)
    assert details.prompt_version.startswith("triage.v2 v")
    assert len(details.prompt_version) == MAX_PROMPT_VERSION_LENGTH


@pytest.mark.parametrize("confidence", ["-0.01", "1.0001", "NaN", "Infinity"])
def test_should_reject_a_confidence_outside_zero_to_one(confidence: str) -> None:
    with pytest.raises(InvalidTriageResultError, match="confidence"):
        TriageDetails.of(
            suggested_action=SuggestedAction.REVIEW,
            source=TriageSource.LLM,
            confidence=Decimal(confidence),
            prompt_version="triage.v1",
        )


def test_should_reject_a_prompt_version_that_is_empty_once_cleaned() -> None:
    with pytest.raises(InvalidTriageResultError, match="prompt version"):
        TriageDetails.of(
            suggested_action=SuggestedAction.REVIEW,
            source=TriageSource.LLM,
            confidence=Decimal("0.5"),
            prompt_version=" \x00 ",
        )


def test_should_build_an_order_failure_explanation_email_notification() -> None:
    notification = Notification.order_failure_explanation(
        notification_id=UUID(int=1),
        customer_id=CUSTOMER_ID,
        order_id=ORDER_ID,
        explanation="  Stock for SKU-002 was short.\n",
        triage=TRIAGE,
        source_event_id=EVENT_ID,
        now=NOW,
    )

    assert notification.type is NotificationType.ORDER_FAILURE_EXPLANATION
    assert notification.channel is NotificationChannel.EMAIL
    assert notification.status is NotificationStatus.SENT
    assert notification.message == "Stock for SKU-002 was short."
    assert notification.triage == TRIAGE
    assert notification.created_at == datetime(2026, 11, 24, 10, 0, 0, 123_000, tzinfo=UTC)
    assert (notification.customer_id, notification.order_id) == (CUSTOMER_ID, ORDER_ID)
    assert notification.source_event_id == EVENT_ID


def test_should_reject_an_order_failure_explanation_at_a_naive_time() -> None:
    with pytest.raises(NaiveTimestampError):
        Notification.order_failure_explanation(
            notification_id=UUID(int=1),
            customer_id=CUSTOMER_ID,
            order_id=ORDER_ID,
            explanation="Stock was short.",
            triage=TRIAGE,
            source_event_id=EVENT_ID,
            now=datetime(2026, 11, 24, 10, 0),
        )


def test_should_leave_other_notifications_without_triage_details() -> None:
    notification = Notification.order_confirmed(
        notification_id=UUID(int=1),
        customer_id=CUSTOMER_ID,
        order_id=ORDER_ID,
        source_event_id=EVENT_ID,
        now=NOW,
    )

    assert notification.triage is None
