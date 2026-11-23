"""Use case: read the recorded notifications (US-04 read API, scoped by US-05). Read-only.

Who may see what is decided by the domain ``AccessScope``; this service only applies it.
"""

from dataclasses import dataclass
from uuid import UUID

from notifications.application.errors import NotificationNotFoundError
from notifications.application.ports import NotificationReader
from notifications.domain.access import AccessScope
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

    async def list(
        self, scope: AccessScope, filters: NotificationFilters, limit: int, offset: int
    ) -> NotificationPage:
        """Return the page of matching notifications in ``scope`` (newest first) and the total.

        A customer filter outside ``scope`` matches nothing: the page is empty, without a query.
        """
        if not scope.permits_customer(filters.customer_id):
            return NotificationPage(items=(), total=0, limit=limit, offset=offset)
        items = await self._reader.find_page(scope, filters, limit, offset)
        total = await self._reader.count(scope, filters)
        return NotificationPage(items=tuple(items), total=total, limit=limit, offset=offset)

    async def get(self, scope: AccessScope, notification_id: UUID) -> Notification:
        """Return the notification with this id.

        Raises:
            NotificationNotFoundError: if it does not exist or is outside ``scope`` (the caller
                cannot tell the two apart).
        """
        notification = await self._reader.find_by_id(scope, notification_id)
        if notification is None:
            raise NotificationNotFoundError(notification_id)
        return notification
