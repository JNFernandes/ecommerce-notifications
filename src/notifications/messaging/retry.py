"""Retry policy and waiting between attempts (research R6)."""

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Capped exponential backoff for processing a consumed message (FR-013)."""

    max_attempts: int
    """Attempts per message, the first included."""
    base_delay: float
    """Pause before the second attempt, in seconds."""
    multiplier: float
    """Growth factor of the pause."""
    max_delay: float
    """Cap on any single pause; also the pause between rounds while dead-lettering fails."""

    def delay_before_attempt(self, attempt: int) -> float:
        """Pause before ``attempt`` (2 or more): ``min(base * multiplier**(attempt-2), max)``.

        Raises:
            ValueError: if ``attempt`` is below 2 (the first attempt has no pause).
        """
        if attempt < 2:
            raise ValueError(f"attempt {attempt} has no pause before it")
        return float(min(self.base_delay * self.multiplier ** (attempt - 2), self.max_delay))


class Sleeper(Protocol):
    """Waits between processing attempts and wakes up early when the service stops."""

    async def sleep(self, seconds: float) -> bool:
        """Wait up to ``seconds``; return ``True`` if a stop was requested."""
        ...


class StopAwareSleeper:
    """Sleeper that ends its wait as soon as the shared stop event is set."""

    def __init__(self, stop_event: asyncio.Event) -> None:
        """Use ``stop_event``, the event the consumer manager sets on shutdown."""
        self._stop_event = stop_event

    async def sleep(self, seconds: float) -> bool:
        """Wait up to ``seconds``; return ``True`` if a stop was requested."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._stop_event.wait()
        return self._stop_event.is_set()
