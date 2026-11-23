"""Ports: the interfaces the application layer needs from the outside world.

Adapters live in ``notifications.infrastructure`` and are wired in ``notifications.main``.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from notifications.domain.access import AccessScope
from notifications.domain.notification import Notification
from notifications.domain.order_context import OrderContext

if TYPE_CHECKING:
    from notifications.application.auth import Principal
    from notifications.application.query_notifications import NotificationFilters


class Clock(Protocol):
    """Source of the current time."""

    def now(self) -> datetime:
        """Return the current time, timezone-aware, in UTC."""
        ...


class IdGenerator(Protocol):
    """Source of new unique identifiers."""

    def new_id(self) -> UUID:
        """Return a new random UUID."""
        ...


class NotificationSender(Protocol):
    """Delivers a stored notification to the customer (simulated: a log line)."""

    def send(self, notification: Notification) -> None:
        """Send ``notification``. Called only after it was stored, never for a duplicate."""
        ...


class NotificationRepository(Protocol):
    """Stores notifications; at most one per source event."""

    async def add(self, notification: Notification) -> bool:
        """Store ``notification``.

        Returns:
            ``True`` if it was stored, ``False`` if a notification with the same
            ``source_event_id`` already exists (a redelivered event; not an error).
        """
        ...


class OrderContextRepository(Protocol):
    """Stores the order contexts; the first context recorded for an order wins."""

    async def add_if_absent(self, context: OrderContext) -> OrderContext | None:
        """Store ``context`` unless one already exists for its order.

        Returns:
            ``None`` if ``context`` was stored, otherwise the existing context (left unchanged).
        """
        ...

    async def get(self, order_id: UUID) -> OrderContext | None:
        """Return the context recorded for ``order_id``, or ``None`` if there is none yet."""
        ...


class AccessTokenVerifier(Protocol):
    """Verifies an access token and tells who the caller is."""

    async def verify(self, token: str) -> "Principal":
        """Return the principal of a valid ``token``.

        Raises:
            InvalidAccessTokenError: if the token is malformed or fails any check.
            AccessTokenUnavailableError: if no signing keys are available to check it.
        """
        ...


class NotificationReader(Protocol):
    """Reads stored notifications for the read API (newest first, id as tie-break).

    Every read is restricted to an ``AccessScope``: a notification outside the scope is never
    returned or counted, whatever the filters say.
    """

    async def find_page(
        self, scope: AccessScope, filters: "NotificationFilters", limit: int, offset: int
    ) -> list[Notification]:
        """Return up to ``limit`` matching notifications in ``scope`` after skipping ``offset``.

        Ordered by creation time descending, then id descending.
        """
        ...

    async def count(self, scope: AccessScope, filters: "NotificationFilters") -> int:
        """Return how many notifications in ``scope`` match ``filters``."""
        ...

    async def find_by_id(self, scope: AccessScope, notification_id: UUID) -> Notification | None:
        """Return the notification with this id if it is in ``scope``, otherwise ``None``."""
        ...
