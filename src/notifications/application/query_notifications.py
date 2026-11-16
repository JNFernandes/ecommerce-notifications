"""Use case: read the recorded notifications (US-04 read API). Read-only; no business rules."""

from dataclasses import dataclass
from uuid import UUID

from notifications.application.errors import NotificationNotFoundError
from notifications.application.ports import NotificationReader
from notifications.domain.notification import Notification, NotificationType


@dataclass(frozen=True, slots=True)
class NotificationFilters:
    """Optional, combinable filters; a notification must match every one given."""

    customer_id: UUID | None = None
    order_id: UUID | None = None
    type: NotificationType | None = None


@dataclass(frozen=True, slots=True)
class NotificationPage:
    """One page of notifications plus the number of all matching notifications."""

    items: tuple[Notification, ...]
    total: int
    limit: int
    offset: int


class NotificationQueryService:
    """Lists and fetches notifications through the ``NotificationReader`` port."""

    def __init__(self, reader: NotificationReader) -> None:
        """Create the service from its reader."""
        self._reader = reader

    async def list(self, filters: NotificationFilters, limit: int, offset: int) -> NotificationPage:
        """Return the page of matching notifications (newest first) and the total count."""
        items = await self._reader.find_page(filters, limit, offset)
        total = await self._reader.count(filters)
        return NotificationPage(items=tuple(items), total=total, limit=limit, offset=offset)

    async def get(self, notification_id: UUID) -> Notification:
        """Return the notification with this id.

        Raises:
            NotificationNotFoundError: if it does not exist.
        """
        notification = await self._reader.find_by_id(notification_id)
        if notification is None:
            raise NotificationNotFoundError(notification_id)
        return notification
