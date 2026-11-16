"""Tests for the Kafka consumer manager against an unreachable broker.

Port 1 on localhost is never a Kafka broker, so these tests exercise the outage path without any
container and pass whether or not the local Kafka is running.
"""

import time

from notifications.application.health import ConsumerState
from notifications.messaging.consumer_manager import KafkaConsumerManager
from tests.support.polling import wait_until

UNREACHABLE_BROKER = "127.0.0.1:1"


def _manager() -> KafkaConsumerManager:
    return KafkaConsumerManager(
        UNREACHABLE_BROKER,
        "notifications-test",
        initial_backoff_seconds=0.1,
        max_backoff_seconds=0.2,
        request_timeout_seconds=2.0,
    )


def test_should_be_stopped_before_it_is_started() -> None:
    assert _manager().state is ConsumerState.STOPPED


async def test_should_return_immediately_from_start_when_kafka_is_unreachable() -> None:
    manager = _manager()
    started = time.monotonic()

    manager.start()

    try:
        assert time.monotonic() - started < 0.5
        assert manager.state is ConsumerState.CONNECTING
    finally:
        await manager.stop()


async def test_should_report_disconnected_and_keep_retrying_when_kafka_is_unreachable() -> None:
    manager = _manager()
    manager.start()
    try:
        await wait_until(
            lambda: manager.state is ConsumerState.DISCONNECTED,
            timeout_seconds=15,
            description="the manager to report DISCONNECTED",
        )
        await wait_until(
            lambda: manager.state is ConsumerState.CONNECTING,
            timeout_seconds=15,
            description="the manager to retry the connection",
        )
    finally:
        await manager.stop()


async def test_should_be_stopped_after_stop_even_while_retrying() -> None:
    manager = _manager()
    manager.start()

    await manager.stop()

    assert manager.state is ConsumerState.STOPPED


async def test_should_ignore_a_second_start_and_a_stop_without_start() -> None:
    manager = _manager()
    await manager.stop()
    manager.start()
    manager.start()

    await manager.stop()

    assert manager.state is ConsumerState.STOPPED
