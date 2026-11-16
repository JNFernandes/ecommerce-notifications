"""Tests for a refund whose order context is missing, through the real retry processor."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from notifications.application.record_payment_refunded import RecordPaymentRefundedService
from notifications.domain.order_context import OrderContext
from notifications.messaging.messages import ProcessResult
from notifications.messaging.payment_refunded import parse_payment_refunded
from notifications.messaging.processor import RetryingMessageProcessor
from notifications.messaging.retry import RetryPolicy
from notifications.messaging.routing import Route, RoutingTable
from tests.support.events import PAYMENT_REFUNDED_TOPIC, payment_refunded_payload, to_json_bytes
from tests.support.fakes import (
    FixedClock,
    InMemoryDeadLetterStore,
    InMemoryNotificationRepository,
    InMemoryOrderContextRepository,
    LateOrderContextRepository,
    RecordingSender,
    RecordingSleeper,
    SequentialIdGenerator,
    consumed_message,
)

NOW = datetime(2026, 11, 16, 10, 5, tzinfo=UTC)
POLICY = RetryPolicy(max_attempts=5, base_delay=1.0, multiplier=2.0, max_delay=10.0)
ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-000000000402")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
REFUND = consumed_message(
    topic=PAYMENT_REFUNDED_TOPIC,
    value=to_json_bytes(payment_refunded_payload(orderId=str(ORDER_ID))),
    offset=7,
)


class Fixture:
    """The refund route behind the real retrying processor, with in-memory collaborators."""

    def __init__(
        self, order_contexts: InMemoryOrderContextRepository | LateOrderContextRepository
    ) -> None:
        self.notifications = InMemoryNotificationRepository()
        self.sleeper = RecordingSleeper()
        self.store = InMemoryDeadLetterStore()
        service = RecordPaymentRefundedService(
            order_contexts,
            self.notifications,
            RecordingSender(),
            FixedClock(NOW),
            SequentialIdGenerator(),
        )
        self.routing = RoutingTable(
            [Route(PAYMENT_REFUNDED_TOPIC, parse_payment_refunded, service.handle)]
        )
        self.processor = RetryingMessageProcessor(POLICY, self.store, self.sleeper, FixedClock(NOW))


async def test_should_retry_then_dead_letter_a_refund_whose_order_context_never_arrives() -> None:
    fixture = Fixture(InMemoryOrderContextRepository())

    result = await fixture.processor.process(REFUND, fixture.routing)

    assert result is ProcessResult.HANDLED
    assert fixture.sleeper.delays == [1.0, 2.0, 4.0, 8.0]
    [dead_letter] = fixture.store.stored.values()
    assert dead_letter.error_type == "OrderContextNotFoundError"
    assert dead_letter.attempts == 5
    assert str(ORDER_ID) in dead_letter.error_message
    assert fixture.notifications.by_source_event == {}


async def test_should_record_the_refund_when_the_context_arrives_during_the_retries() -> None:
    context = OrderContext.from_order_placed(
        order_id=ORDER_ID, customer_id=CUSTOMER_ID, total_amount=Decimal("129.90"), now=NOW
    )
    fixture = Fixture(LateOrderContextRepository(context, missing_calls=2))

    result = await fixture.processor.process(REFUND, fixture.routing)

    assert result is ProcessResult.HANDLED
    assert fixture.sleeper.delays == [1.0, 2.0]
    assert fixture.store.stored == {}
    [notification] = fixture.notifications.by_source_event.values()
    assert notification.customer_id == CUSTOMER_ID
