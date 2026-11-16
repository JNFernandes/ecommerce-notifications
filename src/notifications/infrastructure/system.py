"""System adapters for the clock and id ports."""

from datetime import UTC, datetime
from uuid import UUID, uuid4


class SystemClock:
    """Clock backed by the system time."""

    def now(self) -> datetime:
        """Return the current time in UTC, timezone-aware."""
        return datetime.now(UTC)


class Uuid4Generator:
    """Id generator producing random version 4 UUIDs."""

    def new_id(self) -> UUID:
        """Return a new random UUID."""
        return uuid4()
