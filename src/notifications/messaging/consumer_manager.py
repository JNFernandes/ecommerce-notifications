"""Background manager that keeps the Kafka consumer connected without blocking startup.

No topics are subscribed yet; consumers for the order and payment topics are added by their user
stories. What is in place now is the part every consumer relies on (Principle VIII, startup
resilience): the connection runs in a background task, a broker outage never fails the
application, the manager reconnects with exponential backoff, and its state is exposed for the
readiness endpoint.
"""

import asyncio
import contextlib
import logging

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from notifications.application.health import ConsumerState

logger = logging.getLogger(__name__)


class KafkaConsumerManager:
    """Owns the Kafka consumer lifecycle and reports its connection state."""

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        *,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        check_interval_seconds: float = 10.0,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        """Configure the manager. Nothing connects until :meth:`start` is called."""
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._initial_backoff = initial_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._check_interval = check_interval_seconds
        self._request_timeout = request_timeout_seconds
        self._state = ConsumerState.STOPPED
        self._stop_requested = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> ConsumerState:
        """The current connection state."""
        return self._state

    def start(self) -> None:
        """Start connecting in the background and return immediately."""
        if self._task is not None:
            return
        self._stop_requested.clear()
        self._state = ConsumerState.CONNECTING
        self._task = asyncio.create_task(self._run(), name="kafka-consumer-manager")

    async def stop(self) -> None:
        """Stop the background task and close the consumer."""
        if self._task is None:
            return
        self._stop_requested.set()
        await self._task
        self._task = None
        self._state = ConsumerState.STOPPED

    async def _run(self) -> None:
        backoff = self._initial_backoff
        while not self._stop_requested.is_set():
            self._state = ConsumerState.CONNECTING
            consumer = AIOKafkaConsumer(
                bootstrap_servers=self._bootstrap_servers,
                group_id=self._group_id,
                enable_auto_commit=False,
                auto_offset_reset="earliest",
                request_timeout_ms=int(self._request_timeout * 1000),
            )
            try:
                if await self._connect(consumer):
                    backoff = self._initial_backoff
                    self._state = ConsumerState.CONNECTED
                    logger.info("Kafka consumer connected to %s", self._bootstrap_servers)
                    await self._watch(consumer)
            finally:
                await self._close(consumer)
            if self._stop_requested.is_set():
                break
            self._state = ConsumerState.DISCONNECTED
            logger.warning(
                "Kafka unavailable at %s, retrying in %.1fs", self._bootstrap_servers, backoff
            )
            await self._sleep_unless_stopped(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def _connect(self, consumer: AIOKafkaConsumer) -> bool:
        try:
            async with asyncio.timeout(self._request_timeout):
                await consumer.start()
        except (KafkaError, OSError, TimeoutError) as exc:
            logger.warning("Kafka connection failed: %r", exc)
            return False
        return True

    async def _watch(self, consumer: AIOKafkaConsumer) -> None:
        """Return when a stop is requested or the broker stops answering metadata requests."""
        while not await self._sleep_unless_stopped(self._check_interval):
            try:
                async with asyncio.timeout(self._request_timeout):
                    await consumer.topics()
            except (KafkaError, OSError, TimeoutError) as exc:
                logger.warning("Kafka connection lost: %r", exc)
                return

    async def _close(self, consumer: AIOKafkaConsumer) -> None:
        try:
            await consumer.stop()
        except (KafkaError, OSError) as exc:
            logger.warning("Error while closing the Kafka consumer: %r", exc)

    async def _sleep_unless_stopped(self, seconds: float) -> bool:
        """Wait up to ``seconds``; return ``True`` if a stop was requested meanwhile."""
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(seconds):
                await self._stop_requested.wait()
        return self._stop_requested.is_set()
