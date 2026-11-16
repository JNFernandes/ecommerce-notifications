"""MongoDB repository for notifications (collection ``notifications``)."""

from datetime import datetime
from typing import cast
from uuid import UUID

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

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
