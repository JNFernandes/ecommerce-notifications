"""In-memory fakes for the application layer's ports."""

from notifications.application.health import ConsumerState


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
