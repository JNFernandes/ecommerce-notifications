"""Tests for the consume loop and offset commits, with a fake Kafka consumer."""

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pytest
from aiokafka.errors import CommitFailedError
from aiokafka.structs import TopicPartition

from notifications.application.health import ConsumerState
from notifications.messaging.consumer_manager import KafkaConsumerManager, KafkaRecord
from notifications.messaging.messages import ConsumedMessage, MessageHandler, ProcessResult
from notifications.messaging.routing import Route, RoutingTable
from tests.support.fakes import FailingHandler
from tests.support.polling import wait_until

TOPIC = "orders.order-placed"


@dataclass
class FakeRecord:
    topic: str
    partition: int
    offset: int
    key: bytes | None = None
    value: bytes | None = b"{}"
    timestamp: int = 0


@dataclass
class FakeConsumer:
    """In-memory stand-in for ``AIOKafkaConsumer``."""

    records: list[FakeRecord] = field(default_factory=list)
    commits: list[dict[TopicPartition, int]] = field(default_factory=list)
    fail_commits: bool = False
    subscribed: list[str] = field(default_factory=list)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def topics(self) -> set[str]:
        return {TOPIC}

    async def getmany(
        self, *partitions: object, timeout_ms: int = 0, max_records: int | None = None
    ) -> Mapping[object, Sequence[KafkaRecord]]:
        if not self.records:
            await asyncio.sleep(0.01)
            return {}
        record = self.records.pop(0)
        return {TopicPartition(record.topic, record.partition): [record]}

    async def commit(self, offsets: Mapping[TopicPartition, int]) -> None:
        if self.fail_commits:
            raise CommitFailedError("group rebalanced")
        self.commits.append(dict(offsets))


class ScriptedProcessor:
    """Processor returning scripted results and recording the messages it saw."""

    def __init__(self, results: list[ProcessResult]) -> None:
        self.results = results
        self.seen: list[ConsumedMessage] = []

    async def process(self, message: ConsumedMessage, handler: MessageHandler) -> ProcessResult:
        self.seen.append(message)
        return self.results.pop(0) if self.results else ProcessResult.HANDLED


def _routing() -> RoutingTable:
    """A routing table with one route on ``TOPIC`` whose handler never fails."""
    return RoutingTable([Route(TOPIC, lambda message: message, FailingHandler().handle)])


def _manager(consumer: FakeConsumer, processor: ScriptedProcessor) -> KafkaConsumerManager:
    def factory(topics: Sequence[str]) -> FakeConsumer:
        consumer.subscribed = list(topics)
        return consumer

    return KafkaConsumerManager(
        "unused:9092",
        "notifications-test",
        routing=_routing(),
        processor=processor,
        consumer_factory=factory,
    )


async def test_should_subscribe_to_the_handler_topics_and_commit_the_next_offset() -> None:
    consumer = FakeConsumer(records=[FakeRecord(TOPIC, 1, 41, key=b"k", value=b"v")])
    processor = ScriptedProcessor([ProcessResult.HANDLED])
    manager = _manager(consumer, processor)
    manager.start()
    try:
        await wait_until(lambda: len(consumer.commits) == 1, description="one commit")
    finally:
        await manager.stop()

    assert consumer.subscribed == [TOPIC]
    assert consumer.commits == [{TopicPartition(TOPIC, 1): 42}]
    assert processor.seen[0] == ConsumedMessage(TOPIC, 1, 41, b"k", b"v", 0)


async def test_should_not_commit_and_stop_consuming_when_the_processor_reports_stopped() -> None:
    consumer = FakeConsumer(records=[FakeRecord(TOPIC, 0, 7), FakeRecord(TOPIC, 0, 8)])
    processor = ScriptedProcessor([ProcessResult.STOPPED])
    manager = _manager(consumer, processor)
    manager.start()
    try:
        await wait_until(lambda: len(processor.seen) == 1, description="first message processed")
    finally:
        await manager.stop()

    assert consumer.commits == []
    assert [message.offset for message in processor.seen] == [7]


async def test_should_keep_consuming_when_a_commit_fails() -> None:
    consumer = FakeConsumer(
        records=[FakeRecord(TOPIC, 0, 1), FakeRecord(TOPIC, 0, 2)], fail_commits=True
    )
    processor = ScriptedProcessor([])
    manager = _manager(consumer, processor)
    manager.start()
    try:
        await wait_until(lambda: len(processor.seen) == 2, description="both messages processed")
        assert manager.state is ConsumerState.CONNECTED
    finally:
        await manager.stop()


def test_should_require_a_processor_when_handlers_are_registered() -> None:
    with pytest.raises(ValueError, match="processor"):
        KafkaConsumerManager("unused:9092", "g", routing=_routing())


class HangingConsumer(FakeConsumer):
    """A consumer whose connection attempt never finishes (an unanswering broker)."""

    connecting: bool = False

    async def start(self) -> None:
        self.connecting = True
        await asyncio.Event().wait()


async def test_should_stop_promptly_while_a_connection_attempt_hangs() -> None:
    consumer = HangingConsumer()
    manager = KafkaConsumerManager(
        "unused:9092",
        "notifications-test",
        routing=_routing(),
        processor=ScriptedProcessor([]),
        consumer_factory=lambda topics: consumer,
        request_timeout_seconds=30,
    )
    manager.start()
    await wait_until(lambda: consumer.connecting, description="the connection attempt")
    started = time.monotonic()

    await manager.stop()

    assert time.monotonic() - started < 2
    assert manager.state is ConsumerState.STOPPED
