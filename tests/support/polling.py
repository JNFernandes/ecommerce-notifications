"""Polling helper for asynchronous assertions (Principle VII: no fixed sleeps in tests)."""

import asyncio
import time
from collections.abc import Callable


async def wait_until(
    condition: Callable[[], bool],
    *,
    timeout_seconds: float = 10.0,
    interval_seconds: float = 0.05,
    description: str = "condition",
) -> None:
    """Poll ``condition`` until it is true, failing the test after ``timeout_seconds``."""
    deadline = time.monotonic() + timeout_seconds
    while not condition():
        if time.monotonic() >= deadline:
            raise AssertionError(f"Timed out after {timeout_seconds}s waiting for {description}")
        await asyncio.sleep(interval_seconds)
