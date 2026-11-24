"""Tests for recording the order failure explanation notification."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.application.errors import OrderContextNotFoundError
from notifications.application.record_order_failure_explanation import (
    OrderTriageCompletedCommand,
    RecordOrderFailureExplanationService,
)
from notifications.application.recording import RecordOutcome
from notifications.domain.errors import InvalidExplanationError
from notifications.domain.notification import NotificationType
from notifications.domain.order_context import OrderContext
from notifications.domain.triage import SuggestedAction, TriageDetails, TriageSource
from notifications.messaging.messages import ProcessResult
from notifications.messaging.order_triage_completed import parse_order_triage_completed
from notifications.messaging.processor import RetryingMessageProcessor
from notifications.messaging.retry import RetryPolicy
from notifications.messaging.routing import Route, RoutingTable
from tests.support.events import ORDER_TRIAGE_COMPLETED_TOPIC, order_triage_payload, to_json_bytes
from tests.support.fakes import (
    FixedClock,
    InMemoryDeadLetterStore,
    InMemoryNotificationRepository,
    InMemoryOrderContextRepository,
    Journal,
    LateOrderContextRepository,
    RecordingSender,
    RecordingSleeper,
    SequentialIdGenerator,
    consumed_message,
)

NOW = datetime(2026, 11, 24, 10, 5, tzinfo=UTC)
ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-000000000501")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
CONTEXT = OrderContext.from_order_placed(
    order_id=ORDER_ID, customer_id=CUSTOMER_ID, total_amount=Decimal("129.90"), now=NOW
)
COMMAND = OrderTriageCompletedCommand(
    event_id=UUID("6a1b2c3d-0000-4000-8000-000000000501"),
    order_id=ORDER_ID,
    explanation="Stock for product SKU-002 was short by 3 units.",
    suggested_action=SuggestedAction.RESTOCK,
    source=TriageSource.LLM,
    confidence=Decimal("0.92"),
    prompt_version="triage.v1",
)
POLICY = RetryPolicy(max_attempts=5, base_delay=1.0, multiplier=2.0, max_delay=10.0)


class Fixture:
    """Service under test with in-memory collaborators."""

    def __init__(
        self,
        order_contexts: InMemoryOrderContextRepository | LateOrderContextRepository | None = None,
    ) -> None:
        self.journal = Journal()
        self.order_contexts = order_contexts or InMemoryOrderContextRepository(self.journal)
        self.notifications = InMemoryNotificationRepository(self.journal)
        self.sender = RecordingSender()
        self.service = RecordOrderFailureExplanationService(
            self.order_contexts,
            self.notifications,
            self.sender,
            FixedClock(NOW),
            SequentialIdGenerator(),
        )


def _with_context() -> Fixture:
    fixture = Fixture()
    assert isinstance(fixture.order_contexts, InMemoryOrderContextRepository)
    fixture.order_contexts.by_order[ORDER_ID] = CONTEXT
    return fixture


async def test_should_record_the_explanation_for_the_customer_of_the_order_context() -> None:
    fixture = _with_context()

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.RECORDED
    [notification] = fixture.notifications.by_source_event.values()
    assert notification.type is NotificationType.ORDER_FAILURE_EXPLANATION
    assert notification.customer_id == CUSTOMER_ID
    assert notification.order_id == ORDER_ID
    assert notification.message == COMMAND.explanation
    assert notification.triage == TriageDetails(
        suggested_action=SuggestedAction.RESTOCK,
        source=TriageSource.LLM,
        confidence=Decimal("0.92"),
        prompt_version="triage.v1",
    )
    assert fixture.sender.sent == [notification]
    assert fixture.journal.entries == ["notification"]


async def test_should_not_record_or_send_twice_when_the_event_is_redelivered() -> None:
    fixture = _with_context()

    await fixture.service.handle(COMMAND)
    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.ALREADY_RECORDED
    assert len(fixture.notifications.by_source_event) == 1
    assert len(fixture.sender.sent) == 1


async def test_should_raise_a_retryable_error_when_the_order_is_not_known_yet() -> None:
    fixture = Fixture()

    with pytest.raises(OrderContextNotFoundError) as raised:
        await fixture.service.handle(COMMAND)

    assert raised.value.order_id == ORDER_ID
    assert fixture.notifications.by_source_event == {}
    assert fixture.sender.sent == []


async def test_should_store_nothing_when_the_explanation_is_empty_once_cleaned() -> None:
    fixture = _with_context()
    command = OrderTriageCompletedCommand(
        event_id=COMMAND.event_id,
        order_id=ORDER_ID,
        explanation="\x00 \u200b",
        suggested_action=SuggestedAction.REVIEW,
        source=TriageSource.FALLBACK_TEMPLATE,
        confidence=Decimal("0.1"),
        prompt_version="triage.v1",
    )

    with pytest.raises(InvalidExplanationError):
        await fixture.service.handle(command)

    assert fixture.notifications.by_source_event == {}


def _routed(
    fixture: Fixture,
) -> tuple[RetryingMessageProcessor, RoutingTable, RecordingSleeper, InMemoryDeadLetterStore]:
    """The service's route behind the real retrying processor."""
    sleeper = RecordingSleeper()
    store = InMemoryDeadLetterStore()
    processor = RetryingMessageProcessor(POLICY, store, sleeper, FixedClock(NOW))
    routing = RoutingTable(
        [Route(ORDER_TRIAGE_COMPLETED_TOPIC, parse_order_triage_completed, fixture.service.handle)]
    )
    return processor, routing, sleeper, store


TRIAGE_MESSAGE = consumed_message(
    topic=ORDER_TRIAGE_COMPLETED_TOPIC,
    value=to_json_bytes(order_triage_payload(order_id=ORDER_ID)),
    offset=9,
)


async def test_should_retry_then_dead_letter_an_explanation_whose_order_never_arrives() -> None:
    fixture = Fixture()
    processor, routing, sleeper, store = _routed(fixture)

    result = await processor.process(TRIAGE_MESSAGE, routing)

    assert result is ProcessResult.HANDLED
    assert sleeper.delays == [1.0, 2.0, 4.0, 8.0]
    [dead_letter] = store.stored.values()
    assert dead_letter.error_type == "OrderContextNotFoundError"
    assert dead_letter.attempts == 5
    assert str(ORDER_ID) in dead_letter.error_message
    assert fixture.notifications.by_source_event == {}


async def test_should_record_the_explanation_when_the_order_arrives_during_the_retries() -> None:
    fixture = Fixture(LateOrderContextRepository(CONTEXT, missing_calls=2))
    processor, routing, sleeper, store = _routed(fixture)

    result = await processor.process(TRIAGE_MESSAGE, routing)

    assert result is ProcessResult.HANDLED
    assert sleeper.delays == [1.0, 2.0]
    assert store.stored == {}
    [notification] = fixture.notifications.by_source_event.values()
    assert notification.customer_id == CUSTOMER_ID
