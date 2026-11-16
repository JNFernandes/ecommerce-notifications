"""In-memory fakes for the application ports and the messaging contracts."""

from datetime import datetime, timedelta
from uuid import UUID

from notifications.application.health import ConsumerState
from notifications.domain.notification import Notification
from notifications.domain.order_context import OrderContext
from notifications.messaging.dead_letters import DeadLetter
from notifications.messaging.messages import ConsumedMessage


class Journal:
    """Shared, ordered record of repository writes across fakes."""

    def __init__(self) -> None:
        """Start empty."""
        self.entries: list[str] = []


class InMemoryNotificationRepository:
    """Notification repository keyed by source event id, like the unique index."""

    def __init__(self, journal: Journal | None = None, *, error: Exception | None = None) -> None:
        """Raise ``error`` on every ``add`` when given."""
        self.by_source_event: dict[UUID, Notification] = {}
        self._journal = journal if journal is not None else Journal()
        self._error = error

    async def add(self, notification: Notification) -> bool:
        """Store unless a notification for the same source event exists."""
        if self._error is not None:
            raise self._error
        self._journal.entries.append("notification")
        if notification.source_event_id in self.by_source_event:
            return False
        self.by_source_event[notification.source_event_id] = notification
        return True


class InMemoryOrderContextRepository:
    """Order context repository keyed by order id; the first context wins."""

    def __init__(self, journal: Journal | None = None, *, error: Exception | None = None) -> None:
        """Raise ``error`` on every call when given."""
        self.by_order: dict[UUID, OrderContext] = {}
        self._journal = journal if journal is not None else Journal()
        self._error = error

    async def add_if_absent(self, context: OrderContext) -> OrderContext | None:
        """Store unless a context exists for the order; return the existing one if so."""
        if self._error is not None:
            raise self._error
        self._journal.entries.append("order_context")
        existing = self.by_order.get(context.order_id)
        if existing is None:
            self.by_order[context.order_id] = context
        return existing

    async def get(self, order_id: UUID) -> OrderContext | None:
        """Return the stored context of ``order_id``, if any."""
        if self._error is not None:
            raise self._error
        return self.by_order.get(order_id)


class RecordingSender:
    """Notification sender that records what it sent."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        """Record ``notification``."""
        self.sent.append(notification)


class FixedClock:
    """Clock returning a fixed time, optionally advancing on each call."""

    def __init__(self, now: datetime, step: timedelta = timedelta(0)) -> None:
        """Start at ``now`` and advance by ``step`` after each call."""
        self._now = now
        self._step = step

    def now(self) -> datetime:
        """Return the current fake time."""
        current = self._now
        self._now = current + self._step
        return current


class SequentialIdGenerator:
    """Id generator returning predictable UUIDs 1, 2, 3, ..."""

    def __init__(self) -> None:
        """Start at 1."""
        self._next = 1

    def new_id(self) -> UUID:
        """Return the next predictable UUID."""
        value = UUID(int=self._next)
        self._next += 1
        return value


class FakeDatabaseProbe:
    """Database probe with a fixed answer."""

    def __init__(self, *, reachable: bool) -> None:
        """Answer ``reachable`` to every probe."""
        self.reachable = reachable

    async def is_reachable(self) -> bool:
        """Return the configured answer."""
        return self.reachable


class FakeConsumerStateProvider:
    """Consumer state provider with a settable state."""

    def __init__(self, state: ConsumerState) -> None:
        """Start in ``state``."""
        self.state = state


class InMemoryDeadLetterStore:
    """Dead-letter store keyed by topic, partition and offset; can fail a number of times."""

    def __init__(self, *, failures: int = 0) -> None:
        """Raise ``ConnectionError`` on the first ``failures`` calls."""
        self.stored: dict[tuple[str, int, int], DeadLetter] = {}
        self.calls = 0
        self._failures = failures

    async def add(self, dead_letter: DeadLetter) -> bool:
        """Store unless already stored for the same position."""
        self.calls += 1
        if self.calls <= self._failures:
            raise ConnectionError("dead-letter store unavailable")
        position = (dead_letter.topic, dead_letter.partition, dead_letter.offset)
        if position in self.stored:
            return False
        self.stored[position] = dead_letter
        return True


class RecordingSleeper:
    """Sleeper that never waits: it records the delays and can simulate a stop."""

    def __init__(self, *, stop_after: int | None = None) -> None:
        """Report a stop on the ``stop_after``-th call (1-based); never if ``None``."""
        self.delays: list[float] = []
        self._stop_after = stop_after

    async def sleep(self, seconds: float) -> bool:
        """Record ``seconds`` and return whether a stop is simulated."""
        self.delays.append(seconds)
        return self._stop_after is not None and len(self.delays) >= self._stop_after


class FailingHandler:
    """Message handler that fails a given number of times, then succeeds."""

    def __init__(
        self,
        *,
        failures: int = 0,
        error: Exception | None = None,
    ) -> None:
        """Raise ``error`` (default ``RuntimeError``) on the first ``failures`` calls."""
        self.calls: list[ConsumedMessage] = []
        self._failures = failures
        self._error = error if error is not None else RuntimeError("storage unavailable")

    async def handle(self, message: ConsumedMessage) -> None:
        """Record the call; raise while failures remain."""
        self.calls.append(message)
        if len(self.calls) <= self._failures:
            raise self._error


def consumed_message(
    *,
    value: bytes | None = b"{}",
    key: bytes | None = None,
    topic: str = "orders.order-placed",
    partition: int = 0,
    offset: int = 0,
) -> ConsumedMessage:
    """Build a ``ConsumedMessage`` for tests."""
    return ConsumedMessage(
        topic=topic,
        partition=partition,
        offset=offset,
        key=key,
        value=value,
        timestamp_ms=0,
    )
