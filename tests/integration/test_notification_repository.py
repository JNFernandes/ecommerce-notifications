"""Integration tests for the MongoDB notification repository."""

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from notifications.domain.notification import Notification
from notifications.infrastructure.indexes import ensure_indexes
from notifications.infrastructure.repositories.notifications import (
    MongoNotificationRepository,
    from_document,
    to_document,
)

type Document = dict[str, object]


def _notification(*, source_event_id: UUID | None = None) -> Notification:
    return Notification.order_received(
        notification_id=uuid4(),
        customer_id=uuid4(),
        order_id=UUID("0d1e2f3a-0000-4000-8000-0000000000aa"),
        total_amount=Decimal("129.9"),
        source_event_id=source_event_id or uuid4(),
        now=datetime(2026, 11, 16, 10, 0, 0, 123_456, tzinfo=UTC),
    )


async def test_should_store_the_notification_as_the_documented_document(
    database: AsyncDatabase[Document],
) -> None:
    notification = _notification()

    stored = await MongoNotificationRepository(database).add(notification)

    assert stored is True
    document = await database["notifications"].find_one({"_id": str(notification.id)})
    assert document == {
        "_id": str(notification.id),
        "customerId": str(notification.customer_id),
        "orderId": "0d1e2f3a-0000-4000-8000-0000000000aa",
        "type": "ORDER_RECEIVED",
        "channel": "email",
        "status": "SENT",
        "message": "Your order 0d1e2f3a-0000-4000-8000-0000000000aa for 129.90 was received.",
        "createdAt": datetime(2026, 11, 16, 10, 0, 0, 123_000, tzinfo=UTC),
        "sourceEventId": str(notification.source_event_id),
    }


async def test_should_read_back_an_equal_notification_with_an_aware_utc_creation_time(
    database: AsyncDatabase[Document],
) -> None:
    notification = _notification()
    await MongoNotificationRepository(database).add(notification)

    document = await database["notifications"].find_one({"_id": str(notification.id)})

    assert document is not None
    restored = from_document(document)
    assert restored == notification
    assert restored.created_at.utcoffset() is not None
    assert from_document(to_document(notification)) == notification


async def test_should_report_a_duplicate_source_event_without_storing_a_second_notification(
    database: AsyncDatabase[Document],
) -> None:
    await ensure_indexes(database)
    repository = MongoNotificationRepository(database)
    event_id = uuid4()
    await repository.add(_notification(source_event_id=event_id))

    stored_again = await repository.add(_notification(source_event_id=event_id))

    assert stored_again is False
    assert await database["notifications"].count_documents({"sourceEventId": str(event_id)}) == 1


async def test_should_raise_on_a_duplicate_key_of_another_index(
    database: AsyncDatabase[Document],
) -> None:
    await ensure_indexes(database)
    repository = MongoNotificationRepository(database)
    first = _notification()
    await repository.add(first)
    same_id_other_event = dataclasses.replace(first, source_event_id=uuid4())

    with pytest.raises(DuplicateKeyError):
        await repository.add(same_id_other_event)


def _status_notification(kind: str, source_event_id: UUID) -> Notification:
    if kind == "confirmed":
        return Notification.order_confirmed(
            notification_id=uuid4(),
            customer_id=uuid4(),
            order_id=uuid4(),
            source_event_id=source_event_id,
            now=datetime(2026, 11, 16, 10, 0, tzinfo=UTC),
        )
    return Notification.order_cancelled(
        notification_id=uuid4(),
        customer_id=uuid4(),
        order_id=uuid4(),
        reason="stock rejected: insufficient stock",
        source_event_id=source_event_id,
        now=datetime(2026, 11, 16, 10, 0, tzinfo=UTC),
    )


@pytest.mark.parametrize("kind", ["confirmed", "cancelled"])
async def test_should_keep_one_status_notification_when_its_event_is_redelivered(
    database: AsyncDatabase[Document], kind: str
) -> None:
    await ensure_indexes(database)
    repository = MongoNotificationRepository(database)
    event_id = uuid4()

    first = await repository.add(_status_notification(kind, event_id))
    again = await repository.add(_status_notification(kind, event_id))

    assert (first, again) == (True, False)
    assert await database["notifications"].count_documents({"sourceEventId": str(event_id)}) == 1


@pytest.mark.parametrize("kind", ["confirmed", "cancelled"])
async def test_should_read_back_an_equal_status_notification(
    database: AsyncDatabase[Document], kind: str
) -> None:
    notification = _status_notification(kind, uuid4())
    await MongoNotificationRepository(database).add(notification)

    document = await database["notifications"].find_one({"_id": str(notification.id)})

    assert document is not None
    assert document["type"] == notification.type.value
    assert from_document(document) == notification
