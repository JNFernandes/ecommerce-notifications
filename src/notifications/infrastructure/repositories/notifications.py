"""MongoDB repository for notifications (collection ``notifications``)."""

from datetime import datetime
from typing import cast
from uuid import UUID

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from notifications.application.query_notifications import NotificationFilters
from notifications.domain.access import AccessScope
from notifications.domain.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)
from notifications.infrastructure.indexes import (
    NOTIFICATIONS_COLLECTION,
    NOTIFICATIONS_SOURCE_EVENT_INDEX,
    key_pattern,
)
from notifications.infrastructure.mongo import MongoDocument


def to_document(notification: Notification) -> MongoDocument:
    """Map a notification to its MongoDB document."""
    return {
        "_id": str(notification.id),
        "customerId": str(notification.customer_id),
        "orderId": str(notification.order_id),
        "type": notification.type.value,
        "channel": notification.channel.value,
        "status": notification.status.value,
        "message": notification.message,
        "createdAt": notification.created_at,
        "sourceEventId": str(notification.source_event_id),
    }


def from_document(document: MongoDocument) -> Notification:
    """Map a MongoDB document back to a notification."""
    return Notification(
        id=UUID(cast(str, document["_id"])),
        customer_id=UUID(cast(str, document["customerId"])),
        order_id=UUID(cast(str, document["orderId"])),
        type=NotificationType(cast(str, document["type"])),
        channel=NotificationChannel(cast(str, document["channel"])),
        status=NotificationStatus(cast(str, document["status"])),
        message=cast(str, document["message"]),
        created_at=cast(datetime, document["createdAt"]),
        source_event_id=UUID(cast(str, document["sourceEventId"])),
    )


class MongoNotificationRepository:
    """``NotificationRepository`` backed by the ``notifications`` collection."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Use the ``notifications`` collection of ``database``."""
        self._collection = database[NOTIFICATIONS_COLLECTION]

    async def add(self, notification: Notification) -> bool:
        """Insert the notification.

        Returns:
            ``True`` if stored; ``False`` if a notification for the same source event exists.

        Raises:
            DuplicateKeyError: for a duplicate on any other index (a real failure).
            PyMongoError: for any other database failure.
        """
        try:
            await self._collection.insert_one(to_document(notification))
        except DuplicateKeyError as exc:
            details = exc.details or {}
            if details.get("keyPattern") == key_pattern(NOTIFICATIONS_SOURCE_EVENT_INDEX):
                return False
            raise
        return True

    async def find_page(
        self, scope: AccessScope, filters: NotificationFilters, limit: int, offset: int
    ) -> list[Notification]:
        """Return up to ``limit`` matching notifications in ``scope`` after ``offset``.

        Sorted by ``createdAt`` then ``_id``, both descending; the read indexes of schema
        version 3 serve this order, so MongoDB never sorts in memory.
        """
        query = _query(scope, filters)
        cursor = self._collection.find(query).sort(NEWEST_FIRST).skip(offset).limit(limit)
        return [from_document(document) async for document in cursor]

    async def count(self, scope: AccessScope, filters: NotificationFilters) -> int:
        """Return the exact number of notifications in ``scope`` matching ``filters``."""
        return await self._collection.count_documents(_query(scope, filters))

    async def find_by_id(self, scope: AccessScope, notification_id: UUID) -> Notification | None:
        """Return the notification with this id if it is in ``scope``, or ``None``."""
        query: MongoDocument = {"_id": str(notification_id)}
        if scope.customer_id is not None:
            query["customerId"] = str(scope.customer_id)
        document = await self._collection.find_one(query)
        return None if document is None else from_document(document)


NEWEST_FIRST: list[tuple[str, int]] = [("createdAt", DESCENDING), ("_id", DESCENDING)]
"""Read API order: creation time descending, id descending as tie-break."""


def _query(scope: AccessScope, filters: NotificationFilters) -> MongoDocument:
    """Build the MongoDB filter: the scope and every given filter must match (AND).

    A customer scope always restricts ``customerId``. A customer filter for anyone else then
    matches nothing (defence in depth: the service already answers an empty page without a query).
    """
    query: MongoDocument = {}
    if scope.customer_id is not None:
        conflicting = filters.customer_id not in (None, scope.customer_id)
        query["customerId"] = {"$in": []} if conflicting else str(scope.customer_id)
    elif filters.customer_id is not None:
        query["customerId"] = str(filters.customer_id)
    if filters.order_id is not None:
        query["orderId"] = str(filters.order_id)
    if filters.type is not None:
        query["type"] = filters.type.value
    return query
