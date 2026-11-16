"""Tests for recording the payment refunded notification."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.application.errors import OrderContextNotFoundError
from notifications.application.record_payment_refunded import (
    PaymentRefundedCommand,
    RecordPaymentRefundedService,
)
from notifications.application.recording import RecordOutcome
from notifications.domain.notification import NotificationType
from notifications.domain.order_context import OrderContext
from tests.support.fakes import (
    FixedClock,
    InMemoryNotificationRepository,
    InMemoryOrderContextRepository,
    Journal,
    RecordingSender,
    SequentialIdGenerator,
)

NOW = datetime(2026, 11, 16, 10, 5, tzinfo=UTC)
ORDER_ID = UUID("0d1e2f3a-0000-4000-8000-000000000401")
CUSTOMER_ID = UUID("c1c2c3c4-0000-4000-8000-0000000000cc")
COMMAND = PaymentRefundedCommand(
    event_id=UUID("6a1b2c3d-0000-4000-8000-000000000401"),
    order_id=ORDER_ID,
    amount=Decimal("129.90"),
    currency="EUR",
)


class Fixture:
    """Service under test with in-memory collaborators."""

    def __init__(
        self, *, with_context: bool, notifications: InMemoryNotificationRepository | None = None
    ) -> None:
        self.journal = Journal()
        self.order_contexts = InMemoryOrderContextRepository(self.journal)
        if with_context:
            self.order_contexts.by_order[ORDER_ID] = OrderContext.from_order_placed(
                order_id=ORDER_ID, customer_id=CUSTOMER_ID, total_amount=Decimal("129.90"), now=NOW
            )
        self.notifications = notifications or InMemoryNotificationRepository(self.journal)
        self.sender = RecordingSender()
        self.service = RecordPaymentRefundedService(
            self.order_contexts,
            self.notifications,
            self.sender,
            FixedClock(NOW),
            SequentialIdGenerator(),
        )


async def test_should_record_the_refund_for_the_customer_of_the_order_context() -> None:
    fixture = Fixture(with_context=True)

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.RECORDED
    stored = fixture.notifications.by_source_event[COMMAND.event_id]
    assert stored.type is NotificationType.PAYMENT_REFUNDED
    assert (stored.customer_id, stored.order_id) == (CUSTOMER_ID, ORDER_ID)
    assert stored.message == f"Your payment of 129.90 EUR for order {ORDER_ID} was refunded."
    assert fixture.sender.sent == [stored]


async def test_should_raise_a_retryable_error_naming_the_order_when_its_context_is_missing() -> (
    None
):
    fixture = Fixture(with_context=False)

    with pytest.raises(OrderContextNotFoundError) as raised:
        await fixture.service.handle(COMMAND)

    assert raised.value.order_id == ORDER_ID
    assert str(raised.value) == f"no order context for order {ORDER_ID}"
    assert fixture.notifications.by_source_event == {}
    assert fixture.sender.sent == []
    assert "order_context" not in fixture.journal.entries


async def test_should_not_send_a_redelivered_refund_again() -> None:
    fixture = Fixture(with_context=True)
    await fixture.service.handle(COMMAND)

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.ALREADY_RECORDED
    assert len(fixture.sender.sent) == 1


async def test_should_propagate_a_notification_repository_failure() -> None:
    fixture = Fixture(
        with_context=True,
        notifications=InMemoryNotificationRepository(error=ConnectionError("mongo down")),
    )

    with pytest.raises(ConnectionError):
        await fixture.service.handle(COMMAND)

    assert fixture.sender.sent == []
