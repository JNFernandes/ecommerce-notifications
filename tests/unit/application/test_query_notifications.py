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
from notifications.domain.access import AccessScope
from notifications.domain.notification import Notification, NotificationType
from tests.support.fakes import FakeNotificationReader

START = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
CUSTOMER = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
OTHER_CUSTOMER = UUID("9b2d7c1e-4a5f-4c3b-8e6d-0f1a2b3c4d5e")
ALL = AccessScope.all()
OWN = AccessScope.customer(CUSTOMER)


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

    page = await service.list(ALL, NotificationFilters(), limit=2, offset=1)

    assert [n.created_at for n in page.items] == [
        START + timedelta(minutes=3),
        START + timedelta(minutes=2),
    ]
    assert (page.total, page.limit, page.offset) == (5, 2, 1)


async def test_should_pass_the_scope_filters_limit_and_offset_to_the_reader() -> None:
    reader = FakeNotificationReader([_received(0), _received(1, customer=uuid4())])
    filters = NotificationFilters(customer_id=CUSTOMER, type=NotificationType.ORDER_RECEIVED)

    page = await NotificationQueryService(reader).list(ALL, filters, limit=20, offset=0)

    assert reader.calls == [("find_page", (ALL, filters, 20, 0)), ("count", (ALL, filters))]
    assert page.total == 1


async def test_should_return_an_empty_page_when_nothing_matches() -> None:
    service = NotificationQueryService(FakeNotificationReader([_received(0)]))

    page = await service.list(ALL, NotificationFilters(order_id=uuid4()), limit=20, offset=0)

    assert (page.items, page.total) == ((), 0)


async def test_should_list_only_the_own_notifications_when_scoped_to_a_customer() -> None:
    own = _received(0)
    reader = FakeNotificationReader([own, _received(1, customer=OTHER_CUSTOMER)])

    page = await NotificationQueryService(reader).list(OWN, NotificationFilters(), 20, 0)

    assert (page.items, page.total) == ((own,), 1)


async def test_should_list_the_own_notifications_when_a_customer_filters_by_themselves() -> None:
    reader = FakeNotificationReader([_received(0), _received(1, customer=OTHER_CUSTOMER)])
    filters = NotificationFilters(customer_id=CUSTOMER)

    page = await NotificationQueryService(reader).list(OWN, filters, 20, 0)

    assert page.total == 1


async def test_should_answer_an_empty_page_unqueried_when_a_customer_asks_for_another() -> None:
    reader = FakeNotificationReader([_received(0), _received(1, customer=OTHER_CUSTOMER)])
    filters = NotificationFilters(customer_id=OTHER_CUSTOMER)

    page = await NotificationQueryService(reader).list(OWN, filters, limit=10, offset=5)

    assert (page.items, page.total, page.limit, page.offset) == ((), 0, 10, 5)
    assert reader.calls == []


async def test_should_return_one_notification_by_id() -> None:
    wanted = _received(0)
    service = NotificationQueryService(FakeNotificationReader([_received(1), wanted]))

    assert await service.get(ALL, wanted.id) == wanted


async def test_should_return_the_own_notification_by_id_when_scoped_to_a_customer() -> None:
    wanted = _received(0)
    reader = FakeNotificationReader([wanted])

    assert await NotificationQueryService(reader).get(OWN, wanted.id) == wanted
    assert reader.calls == [("find_by_id", (OWN, wanted.id))]


async def test_should_raise_not_found_naming_the_id() -> None:
    missing = uuid4()

    with pytest.raises(NotificationNotFoundError) as raised:
        await NotificationQueryService(FakeNotificationReader()).get(ALL, missing)

    assert raised.value.notification_id == missing
    assert str(raised.value) == f"notification {missing} not found"


async def test_should_raise_the_same_not_found_when_the_notification_is_another_customers() -> None:
    others = _received(0, customer=OTHER_CUSTOMER)
    service = NotificationQueryService(FakeNotificationReader([others]))

    with pytest.raises(NotificationNotFoundError) as raised:
        await service.get(OWN, others.id)

    assert str(raised.value) == f"notification {others.id} not found"


async def test_should_propagate_a_reader_failure() -> None:
    service = NotificationQueryService(FakeNotificationReader(error=ConnectionError("down")))

    with pytest.raises(ConnectionError):
        await service.list(ALL, NotificationFilters(), limit=20, offset=0)
