"""Tests for the health use cases."""

from notifications.application.health import ComponentStatus, ConsumerState, HealthService
from tests.support.fakes import FakeConsumerStateProvider, FakeDatabaseProbe


def test_should_report_the_service_as_alive() -> None:
    service = HealthService(
        FakeDatabaseProbe(reachable=False), FakeConsumerStateProvider(ConsumerState.STOPPED)
    )

    assert service.check_liveness() is ComponentStatus.UP


async def test_should_be_ready_when_mongodb_is_reachable_and_kafka_is_connected() -> None:
    service = HealthService(
        FakeDatabaseProbe(reachable=True), FakeConsumerStateProvider(ConsumerState.CONNECTED)
    )

    report = await service.check_readiness()

    assert report.status is ComponentStatus.UP
    assert report.mongodb is ComponentStatus.UP
    assert report.kafka is ConsumerState.CONNECTED


async def test_should_stay_ready_and_report_the_kafka_state_when_kafka_is_down() -> None:
    service = HealthService(
        FakeDatabaseProbe(reachable=True), FakeConsumerStateProvider(ConsumerState.DISCONNECTED)
    )

    report = await service.check_readiness()

    assert report.status is ComponentStatus.UP
    assert report.kafka is ConsumerState.DISCONNECTED


async def test_should_not_be_ready_when_mongodb_is_unreachable() -> None:
    service = HealthService(
        FakeDatabaseProbe(reachable=False), FakeConsumerStateProvider(ConsumerState.CONNECTED)
    )

    report = await service.check_readiness()

    assert report.status is ComponentStatus.DOWN
    assert report.mongodb is ComponentStatus.DOWN
