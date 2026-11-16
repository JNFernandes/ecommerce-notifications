"""Message processing with retries, dead letters and holding (FR-012 to FR-018, research R4/R5).

A message counts as handled only when it was stored or dead-lettered. ``process`` never returns
before that, unless a stop is requested; then the offset stays uncommitted and the message is
delivered again after a restart. Kafka commits are cumulative per partition, so moving past an
unhandled message would silently acknowledge it (clarification Q1).
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from notifications.application.ports import Clock
from notifications.messaging.dead_letters import DeadLetterStore, dead_letter_from
from notifications.messaging.messages import (
    ConsumedMessage,
    InvalidMessageError,
    MessageHandler,
    ProcessResult,
)
from notifications.messaging.retry import RetryPolicy, Sleeper

logger = logging.getLogger("notifications.messaging")


@dataclass(frozen=True, slots=True)
class _Failure:
    """The last error of a message that could not be handled, and how often it was tried."""

    error: Exception
    attempts: int
    first_failed_at: datetime


class _Stopped:
    """A stop was requested while waiting between attempts."""


_STOPPED = _Stopped()


class RetryingMessageProcessor:
    """Retries failing messages with capped exponential backoff, then dead-letters them.

    - ``InvalidMessageError``: dead-lettered at once, never retried.
    - Any other error: retried up to ``policy.max_attempts`` attempts, then dead-lettered.
    - Dead-letter store unavailable: wait ``policy.max_delay`` and process the whole message
      again, until it is handled or a stop is requested.
    """

    def __init__(
        self, policy: RetryPolicy, store: DeadLetterStore, sleeper: Sleeper, clock: Clock
    ) -> None:
        """Create the processor."""
        self._policy = policy
        self._store = store
        self._sleeper = sleeper
        self._clock = clock

    async def process(self, message: ConsumedMessage, handler: MessageHandler) -> ProcessResult:
        """Return ``HANDLED`` once the message is stored or dead-lettered, else ``STOPPED``."""
        while True:
            outcome = await self._attempt(message, handler)
            if outcome is None:
                return ProcessResult.HANDLED
            if isinstance(outcome, _Stopped):
                return ProcessResult.STOPPED
            if await self._dead_letter(message, outcome):
                return ProcessResult.HANDLED
            if await self._sleeper.sleep(self._policy.max_delay):
                return ProcessResult.STOPPED

    async def _attempt(
        self, message: ConsumedMessage, handler: MessageHandler
    ) -> _Failure | _Stopped | None:
        """Run the handler with retries; ``None`` means it succeeded."""
        first_failed_at: datetime | None = None
        attempt = 1
        while True:
            try:
                await handler.handle(message)
            except InvalidMessageError as exc:
                return _Failure(exc, attempt, first_failed_at or self._clock.now())
            except Exception as exc:
                # Handled (Principle V): every failure is retried, then dead-lettered and logged.
                first_failed_at = first_failed_at or self._clock.now()
                if attempt >= self._policy.max_attempts:
                    return _Failure(exc, attempt, first_failed_at)
                attempt += 1
                delay = self._policy.delay_before_attempt(attempt)
                logger.warning(
                    "Processing failed, retrying: topic=%s partition=%d offset=%d attempt=%d/%d "
                    "delay=%.1fs error=%s: %s",
                    message.topic,
                    message.partition,
                    message.offset,
                    attempt,
                    self._policy.max_attempts,
                    delay,
                    type(exc).__name__,
                    exc,
                )
                if await self._sleeper.sleep(delay):
                    return _STOPPED
            else:
                return None

    async def _dead_letter(self, message: ConsumedMessage, failure: _Failure) -> bool:
        """Store the dead letter; return ``False`` if the store is unavailable."""
        dead_letter = dead_letter_from(
            message,
            error=failure.error,
            attempts=failure.attempts,
            first_failed_at=failure.first_failed_at,
            now=self._clock.now(),
        )
        try:
            stored = await self._store.add(dead_letter)
        except Exception as exc:
            # Handled (Principle V): logged, then the whole message is processed again (Q1).
            logger.error(
                "Dead-letter store unavailable, holding message: topic=%s partition=%d offset=%d "
                "retryIn=%.1fs error=%s: %s",
                message.topic,
                message.partition,
                message.offset,
                self._policy.max_delay,
                type(exc).__name__,
                exc,
            )
            return False
        if not stored:
            logger.info(
                "Message already dead-lettered: topic=%s partition=%d offset=%d",
                message.topic,
                message.partition,
                message.offset,
            )
            return True
        logger.error(
            "Message dead-lettered: topic=%s partition=%d offset=%d attempts=%d errorType=%s "
            "error=%s",
            message.topic,
            message.partition,
            message.offset,
            failure.attempts,
            dead_letter.error_type,
            dead_letter.error_message,
            exc_info=None if isinstance(failure.error, InvalidMessageError) else failure.error,
        )
        return True
