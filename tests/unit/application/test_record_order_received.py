"""Tests for recording the order received notification."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from notifications.application.record_order_received import (
    OrderPlacedCommand,
    RecordOrderReceivedService,
    RecordOutcome,
)
from notifications.domain.errors import InvalidOrderTotalError
from notifications.domain.notification import NotificationStatus, NotificationType
from notifications.domain.order_context import OrderContext
from tests.support.fakes import (
    FixedClock,
    InMemoryNotificationRepository,
    InMemoryOrderContextRepository,
    Journal,
    RecordingSender,
    SequentialIdGenerator,
)

NOW = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
COMMAND = OrderPlacedCommand(
    event_id=UUID("6a1b2c3d-0000-4000-8000-000000000001"),
    order_id=UUID("0d1e2f3a-0000-4000-8000-0000000000aa"),
    customer_id=UUID("c1c2c3c4-0000-4000-8000-0000000000cc"),
    total_amount=Decimal("129.9"),
)


class Fixture:
    """Service under test with in-memory collaborators sharing one write journal."""

    def __init__(
        self,
        notifications: InMemoryNotificationRepository | None = None,
        order_contexts: InMemoryOrderContextRepository | None = None,
    ) -> None:
        self.journal = Journal()
        self.notifications = notifications or InMemoryNotificationRepository(self.journal)
        self.order_contexts = order_contexts or InMemoryOrderContextRepository(self.journal)
        self.sender = RecordingSender()
        self.service = RecordOrderReceivedService(
            self.order_contexts,
            self.notifications,
            self.sender,
            FixedClock(NOW),
            SequentialIdGenerator(),
        )


async def test_should_store_one_order_received_notification_and_send_it_once() -> None:
    fixture = Fixture()

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.RECORDED
    stored = fixture.notifications.by_source_event[COMMAND.event_id]
    assert stored.type is NotificationType.ORDER_RECEIVED
    assert stored.status is NotificationStatus.SENT
    assert (stored.customer_id, stored.order_id) == (COMMAND.customer_id, COMMAND.order_id)
    assert stored.message == f"Your order {COMMAND.order_id} for 129.90 was received."
    assert stored.created_at == NOW
    assert fixture.sender.sent == [stored]


async def test_should_propagate_a_repository_failure_without_sending() -> None:
    fixture = Fixture(InMemoryNotificationRepository(error=ConnectionError("mongo down")))

    with pytest.raises(ConnectionError):
        await fixture.service.handle(COMMAND)

    assert fixture.sender.sent == []


async def test_should_reject_a_negative_total_without_storing_or_sending() -> None:
    fixture = Fixture()
    command = OrderPlacedCommand(
        event_id=COMMAND.event_id,
        order_id=COMMAND.order_id,
        customer_id=COMMAND.customer_id,
        total_amount=Decimal("-1"),
    )

    with pytest.raises(InvalidOrderTotalError):
        await fixture.service.handle(command)

    assert fixture.notifications.by_source_event == {}
    assert fixture.sender.sent == []


async def test_should_neither_store_nor_send_again_when_the_event_was_already_recorded() -> None:
    fixture = Fixture()
    await fixture.service.handle(COMMAND)

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.ALREADY_RECORDED
    assert len(fixture.notifications.by_source_event) == 1
    assert len(fixture.sender.sent) == 1


async def test_should_log_the_redelivered_event_at_info_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = Fixture()
    await fixture.service.handle(COMMAND)
    caplog.set_level("INFO", logger="notifications.application")

    await fixture.service.handle(COMMAND)

    assert any(
        record.getMessage()
        == f"Event already recorded: type=ORDER_RECEIVED sourceEvent={COMMAND.event_id} "
        f"order={COMMAND.order_id}"
        for record in caplog.records
    )


async def test_should_store_the_order_context_with_the_exact_total() -> None:
    fixture = Fixture()

    await fixture.service.handle(COMMAND)

    assert fixture.order_contexts.by_order[COMMAND.order_id] == OrderContext(
        order_id=COMMAND.order_id,
        customer_id=COMMAND.customer_id,
        total_amount=Decimal("129.9"),
        recorded_at=NOW,
    )


async def test_should_store_the_order_context_before_the_notification() -> None:
    fixture = Fixture()

    await fixture.service.handle(COMMAND)

    assert fixture.journal.entries == ["order_context", "notification"]


async def test_should_record_the_notification_without_warning_when_the_same_context_exists(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = Fixture()
    await fixture.order_contexts.add_if_absent(
        OrderContext.from_order_placed(
            order_id=COMMAND.order_id,
            customer_id=COMMAND.customer_id,
            total_amount=Decimal("129.90"),
            now=NOW,
        )
    )

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.RECORDED
    assert not [record for record in caplog.records if record.levelname == "WARNING"]


async def test_should_keep_the_first_context_and_warn_when_a_conflicting_one_arrives(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fixture = Fixture()
    first = OrderContext.from_order_placed(
        order_id=COMMAND.order_id,
        customer_id=UUID("c1c2c3c4-0000-4000-8000-0000000000dd"),
        total_amount=Decimal("10"),
        now=NOW,
    )
    await fixture.order_contexts.add_if_absent(first)

    outcome = await fixture.service.handle(COMMAND)

    assert outcome is RecordOutcome.RECORDED
    assert fixture.order_contexts.by_order[COMMAND.order_id] == first
    [warning] = [record for record in caplog.records if record.levelname == "WARNING"]
    assert f"order={COMMAND.order_id}" in warning.getMessage()
    assert "keeping the first" in warning.getMessage()


async def test_should_neither_store_the_notification_nor_send_when_the_context_store_fails() -> (
    None
):
    fixture = Fixture(order_contexts=InMemoryOrderContextRepository(error=ConnectionError("down")))

    with pytest.raises(ConnectionError):
        await fixture.service.handle(COMMAND)

    assert fixture.notifications.by_source_event == {}
    assert fixture.sender.sent == []
