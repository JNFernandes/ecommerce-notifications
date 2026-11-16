"""Ports: the interfaces the application layer needs from the outside world.

Adapters live in ``notifications.infrastructure`` and are wired in ``notifications.main``.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from notifications.domain.notification import Notification
from notifications.domain.order_context import OrderContext


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
