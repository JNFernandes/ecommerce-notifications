"""Tests for the notification query service."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from notifications.application.errors import NotificationNotFoundError
from notifications.application.query_notifications import (
    NotificationFilters,
    NotificationQueryService,
)
from notifications.domain.notification import Notification, NotificationType
from tests.support.fakes import FakeNotificationReader

START = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
CUSTOMER = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")


def _received(minutes: int, customer: UUID = CUSTOMER) -> Notification:
    return Notification.order_received(
        notification_id=uuid4(),
        customer_id=customer,
        order_id=uuid4(),
        total_amount=Decimal("1"),
        source_event_id=uuid4(),
        now=START + timedelta(minutes=minutes),
    )


async def test_should_return_the_page_newest_first_with_the_total() -> None:
    service = NotificationQueryService(FakeNotificationReader([_received(m) for m in range(5)]))

    page = await service.list(NotificationFilters(), limit=2, offset=1)

    assert [n.created_at for n in page.items] == [
        START + timedelta(minutes=3),
        START + timedelta(minutes=2),
    ]
    assert (page.total, page.limit, page.offset) == (5, 2, 1)


async def test_should_pass_the_filters_limit_and_offset_to_the_reader() -> None:
    reader = FakeNotificationReader([_received(0), _received(1, customer=uuid4())])
    filters = NotificationFilters(customer_id=CUSTOMER, type=NotificationType.ORDER_RECEIVED)

    page = await NotificationQueryService(reader).list(filters, limit=20, offset=0)

    assert reader.calls == [("find_page", (filters, 20, 0)), ("count", filters)]
    assert page.total == 1


async def test_should_return_an_empty_page_when_nothing_matches() -> None:
    service = NotificationQueryService(FakeNotificationReader([_received(0)]))

    page = await service.list(NotificationFilters(order_id=uuid4()), limit=20, offset=0)

    assert (page.items, page.total) == ((), 0)


async def test_should_return_one_notification_by_id() -> None:
    wanted = _received(0)
    service = NotificationQueryService(FakeNotificationReader([_received(1), wanted]))

    assert await service.get(wanted.id) == wanted


async def test_should_raise_not_found_naming_the_id() -> None:
    missing = uuid4()

    with pytest.raises(NotificationNotFoundError) as raised:
        await NotificationQueryService(FakeNotificationReader()).get(missing)

    assert raised.value.notification_id == missing
    assert str(raised.value) == f"notification {missing} not found"


async def test_should_propagate_a_reader_failure() -> None:
    service = NotificationQueryService(FakeNotificationReader(error=ConnectionError("down")))

    with pytest.raises(ConnectionError):
        await service.list(NotificationFilters(), limit=20, offset=0)
