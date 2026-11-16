"""Background manager that connects the Kafka consumer, consumes, and commits offsets.

Principle VIII, startup resilience: the connection runs in a background task, a broker outage
never fails the application, the manager reconnects with exponential backoff, and its state is
exposed for the readiness endpoint.

Principle III, write flow: messages are processed one at a time (clarification Q2). The offset
of a message is committed only when the processor returns ``HANDLED``, which means the message
was stored or dead-lettered. ``STOPPED`` leaves the offset uncommitted, so the message is
delivered again after a restart.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
from aiokafka.structs import TopicPartition

from notifications.application.health import ConsumerState
from notifications.messaging.messages import (
    ConsumedMessage,
    MessageProcessor,
    ProcessResult,
)
from notifications.messaging.routing import RoutingTable

logger = logging.getLogger(__name__)

_POLL_TIMEOUT_MS = 500


class KafkaRecord(Protocol):
    """The fields of an aiokafka ``ConsumerRecord`` that the manager reads."""

    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    timestamp: int


class KafkaConsumerClient(Protocol):
    """The subset of ``AIOKafkaConsumer`` used by the manager (lets tests use a fake)."""

    async def start(self) -> None:
        """Connect to the cluster and join the consumer group."""
        ...

    async def stop(self) -> None:
        """Leave the group and close the connections."""
        ...

    async def topics(self) -> set[str]:
        """Fetch cluster metadata; used as a connectivity check."""
        ...

    async def getmany(
        self, *partitions: object, timeout_ms: int = 0, max_records: int | None = None
    ) -> Mapping[object, Sequence[KafkaRecord]]:
        """Fetch the next records, waiting at most ``timeout_ms``."""
        ...

    async def commit(self, offsets: Mapping[TopicPartition, int]) -> None:
        """Commit the given offsets for the consumer group."""
        ...


type ConsumerFactory = Callable[[Sequence[str]], KafkaConsumerClient]


class KafkaConsumerManager:
    """Owns the Kafka consumer lifecycle and reports its connection state."""

    def __init__(
        self,
        bootstrap_servers: str,
        group_id: str,
        *,
        routing: RoutingTable | None = None,
        processor: MessageProcessor | None = None,
        stop_event: asyncio.Event | None = None,
        consumer_factory: ConsumerFactory | None = None,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        check_interval_seconds: float = 10.0,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        """Configure the manager. Nothing connects until :meth:`start` is called.

        Args:
            bootstrap_servers: Comma-separated Kafka brokers.
            group_id: Consumer group id.
            routing: Routing table of the consumed topics (topic -> parser + service method).
            processor: Runs the routing table for each message; required with a routing table.
            stop_event: Event set by :meth:`stop`. Pass the same event to the processor's
                sleeper so that retry pauses end promptly on shutdown.
            consumer_factory: Builds the consumer for the given topics (tests pass a fake).
            initial_backoff_seconds: First pause after a failed connection.
            max_backoff_seconds: Cap on the reconnection pause.
            check_interval_seconds: How often the broker connection is verified.
            request_timeout_seconds: Timeout for connecting and for the connectivity check.
        """
        if routing is not None and processor is None:
            raise ValueError("a processor is required with a routing table")
        self._bootstrap_servers = bootstrap_servers
        self._group_id = group_id
        self._routing = routing
        self._processor = processor
        self._stop_requested = stop_event if stop_event is not None else asyncio.Event()
        self._consumer_factory = consumer_factory or self._create_consumer
        self._initial_backoff = initial_backoff_seconds
        self._max_backoff = max_backoff_seconds
        self._check_interval = check_interval_seconds
        self._request_timeout = request_timeout_seconds
        self._state = ConsumerState.STOPPED
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

    def _create_consumer(self, topics: Sequence[str]) -> KafkaConsumerClient:
        consumer: KafkaConsumerClient = AIOKafkaConsumer(
            *topics,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            request_timeout_ms=int(self._request_timeout * 1000),
        )
        return consumer

    async def _run(self) -> None:
        backoff = self._initial_backoff
        while not self._stop_requested.is_set():
            self._state = ConsumerState.CONNECTING
            consumer = self._consumer_factory(self._routing.topics if self._routing else ())
            try:
                if await self._connect(consumer):
                    backoff = self._initial_backoff
                    self._state = ConsumerState.CONNECTED
                    logger.info("Kafka consumer connected to %s", self._bootstrap_servers)
                    await self._consume(consumer)
            except Exception:
                # Handled (Principle V): an unexpected error must not kill the background task.
                # The uncommitted message is delivered again after the reconnection.
                logger.exception("Kafka consumer loop failed; reconnecting")
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

    async def _connect(self, consumer: KafkaConsumerClient) -> bool:
        """Start the consumer; give up on timeout, on a connection error, or when stopping.

        The attempt races the stop event, so ``stop()`` returns promptly even when the broker
        does not answer at all (research R9).
        """
        connecting = asyncio.ensure_future(consumer.start())
        stopping = asyncio.ensure_future(self._stop_requested.wait())
        try:
            done, _ = await asyncio.wait(
                {connecting, stopping},
                timeout=self._request_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stopping.cancel()
        if connecting not in done:
            connecting.cancel()
            with contextlib.suppress(asyncio.CancelledError, KafkaError, OSError):
                await connecting
            if not self._stop_requested.is_set():
                logger.warning("Kafka connection timed out after %.1fs", self._request_timeout)
            return False
        error = connecting.exception()
        if isinstance(error, KafkaError | OSError):
            logger.warning("Kafka connection failed: %r", error)
            return False
        if error is not None:
            raise error
        return True

    async def _consume(self, consumer: KafkaConsumerClient) -> None:
        """Return when a stop is requested or the broker connection is lost."""
        next_check = time.monotonic() + self._check_interval
        while not self._stop_requested.is_set():
            if self._routing is not None:
                if not await self._poll_once(consumer):
                    return
            elif await self._sleep_unless_stopped(max(0.0, next_check - time.monotonic())):
                return
            if time.monotonic() >= next_check:
                if not await self._broker_answers(consumer):
                    return
                next_check = time.monotonic() + self._check_interval

    async def _poll_once(self, consumer: KafkaConsumerClient) -> bool:
        """Fetch and process at most one record; return ``False`` to leave the consume loop."""
        try:
            batch = await consumer.getmany(timeout_ms=_POLL_TIMEOUT_MS, max_records=1)
        except KafkaError as exc:
            logger.warning("Kafka fetch failed: %r", exc)
            return False
        for records in batch.values():
            for record in records:
                if not await self._process(consumer, record):
                    return False
        return True

    async def _process(self, consumer: KafkaConsumerClient, record: KafkaRecord) -> bool:
        """Process one record and commit it when handled; return ``False`` on stop."""
        if self._processor is None or self._routing is None:  # pragma: no cover - guarded
            raise RuntimeError("no processor or routing table configured")
        message = ConsumedMessage(
            topic=record.topic,
            partition=record.partition,
            offset=record.offset,
            key=record.key,
            value=record.value,
            timestamp_ms=record.timestamp,
        )
        result = await self._processor.process(message, self._routing)
        if result is ProcessResult.STOPPED:
            return False
        try:
            await consumer.commit(
                {TopicPartition(record.topic, record.partition): record.offset + 1}
            )
        except KafkaError as exc:
            logger.warning(
                "Offset commit failed; the message will be redelivered: "
                "topic=%s partition=%d offset=%d error=%r",
                record.topic,
                record.partition,
                record.offset,
                exc,
            )
        return True

    async def _broker_answers(self, consumer: KafkaConsumerClient) -> bool:
        try:
            async with asyncio.timeout(self._request_timeout):
                await consumer.topics()
        except (KafkaError, OSError, TimeoutError) as exc:
            logger.warning("Kafka connection lost: %r", exc)
            return False
        return True

    async def _close(self, consumer: KafkaConsumerClient) -> None:
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
