"""Integration tests for the read queries of the MongoDB notification repository."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from notifications.application.query_notifications import NotificationFilters
from notifications.domain.access import AccessScope
from notifications.domain.notification import Notification, NotificationType
from notifications.infrastructure.indexes import ensure_indexes
from notifications.infrastructure.repositories.notifications import (
    NEWEST_FIRST,
    MongoNotificationRepository,
    _query,
)

type Document = dict[str, object]

START = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
CUSTOMER_A, CUSTOMER_B = uuid4(), uuid4()
ORDER_1, ORDER_2 = uuid4(), uuid4()
ALL = AccessScope.all()


def _notification(
    minutes: int, customer: UUID, order: UUID, kind: NotificationType
) -> Notification:
    now = START + timedelta(minutes=minutes)
    common = {"notification_id": uuid4(), "customer_id": customer, "order_id": order}
    if kind is NotificationType.ORDER_RECEIVED:
        return Notification.order_received(
            **common, total_amount=Decimal("1"), source_event_id=uuid4(), now=now
        )
    if kind is NotificationType.ORDER_CONFIRMED:
        return Notification.order_confirmed(**common, source_event_id=uuid4(), now=now)
    return Notification.order_cancelled(**common, reason=None, source_event_id=uuid4(), now=now)


SEED = [
    _notification(0, CUSTOMER_A, ORDER_1, NotificationType.ORDER_RECEIVED),
    _notification(1, CUSTOMER_A, ORDER_1, NotificationType.ORDER_CONFIRMED),
    _notification(2, CUSTOMER_B, ORDER_2, NotificationType.ORDER_RECEIVED),
    # three with the same creation time: ordered by id descending
    _notification(3, CUSTOMER_B, ORDER_2, NotificationType.ORDER_CANCELLED),
    _notification(3, CUSTOMER_A, ORDER_1, NotificationType.ORDER_CANCELLED),
    _notification(3, CUSTOMER_B, ORDER_2, NotificationType.ORDER_CONFIRMED),
]


def _expected(filters: NotificationFilters, scope: AccessScope = ALL) -> list[Notification]:
    found = [
        n
        for n in SEED
        if (scope.customer_id is None or n.customer_id == scope.customer_id)
        and (filters.customer_id is None or n.customer_id == filters.customer_id)
        and (filters.order_id is None or n.order_id == filters.order_id)
        and (filters.type is None or n.type is filters.type)
    ]
    return sorted(found, key=lambda n: (n.created_at, str(n.id)), reverse=True)


@pytest.fixture
async def repository(database: AsyncDatabase[Document]) -> MongoNotificationRepository:
    await ensure_indexes(database)
    repository = MongoNotificationRepository(database)
    for notification in SEED:
        await repository.add(notification)
    return repository


@pytest.mark.parametrize(
    "filters",
    [
        NotificationFilters(),
        NotificationFilters(customer_id=CUSTOMER_A),
        NotificationFilters(order_id=ORDER_2),
        NotificationFilters(type=NotificationType.ORDER_RECEIVED),
        NotificationFilters(customer_id=CUSTOMER_B, type=NotificationType.ORDER_CONFIRMED),
        NotificationFilters(customer_id=CUSTOMER_A, order_id=ORDER_2),
    ],
)
async def test_should_return_matching_notifications_newest_first_with_id_tie_break(
    repository: MongoNotificationRepository, filters: NotificationFilters
) -> None:
    page = await repository.find_page(ALL, filters, limit=100, offset=0)

    assert page == _expected(filters)
    assert await repository.count(ALL, filters) == len(_expected(filters))


async def test_should_return_each_notification_exactly_once_when_walking_all_pages(
    repository: MongoNotificationRepository,
) -> None:
    seen: list[Notification] = []
    for offset in range(0, len(SEED), 4):
        seen += await repository.find_page(ALL, NotificationFilters(), limit=4, offset=offset)

    assert seen == _expected(NotificationFilters())
    assert len({n.id for n in seen}) == len(SEED)


async def test_should_return_an_empty_page_past_the_end_with_the_real_total(
    repository: MongoNotificationRepository,
) -> None:
    assert await repository.find_page(ALL, NotificationFilters(), limit=20, offset=50) == []
    assert await repository.count(ALL, NotificationFilters()) == len(SEED)


async def test_should_find_one_notification_by_id(
    repository: MongoNotificationRepository,
) -> None:
    assert await repository.find_by_id(ALL, SEED[2].id) == SEED[2]
    assert await repository.find_by_id(ALL, uuid4()) is None


@pytest.mark.parametrize(
    "filters",
    [
        NotificationFilters(),
        NotificationFilters(customer_id=CUSTOMER_A),
        NotificationFilters(order_id=ORDER_1),
        NotificationFilters(order_id=ORDER_2),
        NotificationFilters(type=NotificationType.ORDER_CANCELLED),
    ],
)
async def test_should_return_only_the_scoped_customers_notifications_when_scoped(
    repository: MongoNotificationRepository, filters: NotificationFilters
) -> None:
    scope = AccessScope.customer(CUSTOMER_A)

    page = await repository.find_page(scope, filters, limit=100, offset=0)

    assert page == _expected(filters, scope)
    assert all(n.customer_id == CUSTOMER_A for n in page)
    assert await repository.count(scope, filters) == len(page)


async def test_should_match_nothing_when_the_customer_filter_is_outside_the_scope(
    repository: MongoNotificationRepository,
) -> None:
    scope = AccessScope.customer(CUSTOMER_A)
    filters = NotificationFilters(customer_id=CUSTOMER_B)

    assert await repository.find_page(scope, filters, limit=100, offset=0) == []
    assert await repository.count(scope, filters) == 0


async def test_should_find_a_notification_by_id_only_inside_the_scope(
    repository: MongoNotificationRepository,
) -> None:
    owned_by_b = SEED[2]

    assert (
        await repository.find_by_id(AccessScope.customer(CUSTOMER_B), owned_by_b.id) == owned_by_b
    )
    assert await repository.find_by_id(AccessScope.customer(CUSTOMER_A), owned_by_b.id) is None


def _stages(plan: object) -> list[str]:
    if isinstance(plan, dict):
        found = [str(plan["stage"])] if "stage" in plan else []
        return found + [s for value in plan.values() for s in _stages(value)]
    if isinstance(plan, list):
        return [s for item in plan for s in _stages(item)]
    return []


@pytest.mark.parametrize(
    ("scope", "filters"),
    [
        (ALL, NotificationFilters()),
        (ALL, NotificationFilters(customer_id=CUSTOMER_A)),
        (ALL, NotificationFilters(order_id=ORDER_1)),
        (ALL, NotificationFilters(type=NotificationType.ORDER_CANCELLED)),
        (AccessScope.customer(CUSTOMER_A), NotificationFilters()),
        (AccessScope.customer(CUSTOMER_A), NotificationFilters(order_id=ORDER_1)),
        (
            AccessScope.customer(CUSTOMER_A),
            NotificationFilters(type=NotificationType.ORDER_CANCELLED),
        ),
    ],
)
async def test_should_serve_each_filter_from_an_index_without_sorting_in_memory(
    repository: MongoNotificationRepository,
    database: AsyncDatabase[Document],
    scope: AccessScope,
    filters: NotificationFilters,
) -> None:
    query = _query(scope, filters)
    explained = await database["notifications"].find(query).sort(NEWEST_FIRST).limit(20).explain()

    planner = explained["queryPlanner"]
    assert isinstance(planner, dict)
    stages = _stages(planner["winningPlan"])
    assert "IXSCAN" in stages
    assert "SORT" not in stages
