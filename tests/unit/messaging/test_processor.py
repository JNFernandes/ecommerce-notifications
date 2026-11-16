"""Tests for the retrying, dead-lettering message processor."""

from datetime import UTC, datetime, timedelta

from notifications.messaging.messages import InvalidMessageError, ProcessResult
from notifications.messaging.processor import RetryingMessageProcessor
from notifications.messaging.retry import RetryPolicy
from tests.support.fakes import (
    FailingHandler,
    FixedClock,
    InMemoryDeadLetterStore,
    RecordingSleeper,
    consumed_message,
)

POLICY = RetryPolicy(max_attempts=5, base_delay=1.0, multiplier=2.0, max_delay=10.0)
START = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
MESSAGE = consumed_message(value=b'{"x": 1}', partition=2, offset=42)


class Fixture:
    """Processor under test with in-memory collaborators."""

    def __init__(self, *, store_failures: int = 0, stop_after: int | None = None) -> None:
        self.store = InMemoryDeadLetterStore(failures=store_failures)
        self.sleeper = RecordingSleeper(stop_after=stop_after)
        self.clock = FixedClock(START, step=timedelta(seconds=1))
        self.processor = RetryingMessageProcessor(POLICY, self.store, self.sleeper, self.clock)


async def test_should_report_handled_when_the_first_attempt_succeeds() -> None:
    fixture = Fixture()
    handler = FailingHandler()

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.HANDLED
    assert len(handler.calls) == 1
    assert fixture.sleeper.delays == []
    assert fixture.store.stored == {}


async def test_should_retry_with_growing_pauses_until_an_attempt_succeeds() -> None:
    fixture = Fixture()
    handler = FailingHandler(failures=2)

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.HANDLED
    assert len(handler.calls) == 3
    assert fixture.sleeper.delays == [1.0, 2.0]
    assert fixture.store.stored == {}


async def test_should_dead_letter_the_message_after_the_last_attempt_fails() -> None:
    fixture = Fixture()
    handler = FailingHandler(failures=99, error=ConnectionError("mongo down"))

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.HANDLED
    assert len(handler.calls) == 5
    assert fixture.sleeper.delays == [1.0, 2.0, 4.0, 8.0]
    [dead_letter] = fixture.store.stored.values()
    assert (dead_letter.partition, dead_letter.offset) == (2, 42)
    assert (dead_letter.error_type, dead_letter.error_message) == ("ConnectionError", "mongo down")
    assert dead_letter.attempts == 5
    assert dead_letter.first_failed_at == START
    assert dead_letter.dead_lettered_at > dead_letter.first_failed_at


async def test_should_dead_letter_an_invalid_message_at_once_without_retrying() -> None:
    fixture = Fixture()
    handler = FailingHandler(failures=99, error=InvalidMessageError("customerId: Field required"))

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.HANDLED
    assert len(handler.calls) == 1
    assert fixture.sleeper.delays == []
    [dead_letter] = fixture.store.stored.values()
    assert dead_letter.attempts == 1
    assert dead_letter.error_message == "customerId: Field required"


async def test_should_treat_an_already_dead_lettered_message_as_handled() -> None:
    fixture = Fixture()
    invalid = FailingHandler(failures=99, error=InvalidMessageError("bad"))
    await fixture.processor.process(MESSAGE, invalid)

    result = await fixture.processor.process(MESSAGE, invalid)

    assert result is ProcessResult.HANDLED
    assert len(fixture.store.stored) == 1


async def test_should_hold_and_process_the_whole_message_again_while_dead_lettering_fails() -> None:
    fixture = Fixture(store_failures=2)
    handler = FailingHandler(failures=99, error=InvalidMessageError("bad"))

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.HANDLED
    assert fixture.store.calls == 3
    assert len(handler.calls) == 3
    assert fixture.sleeper.delays == [10.0, 10.0]
    assert len(fixture.store.stored) == 1


async def test_should_succeed_in_a_later_round_when_the_cause_went_away() -> None:
    fixture = Fixture(store_failures=1)
    handler = FailingHandler(failures=5)

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.HANDLED
    assert len(handler.calls) == 6
    assert fixture.store.stored == {}


async def test_should_stop_without_dead_lettering_when_a_stop_interrupts_a_retry_pause() -> None:
    fixture = Fixture(stop_after=2)
    handler = FailingHandler(failures=99)

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.STOPPED
    assert len(handler.calls) == 2
    assert fixture.store.calls == 0


async def test_should_stop_when_a_stop_interrupts_the_hold_pause() -> None:
    fixture = Fixture(store_failures=99, stop_after=1)
    handler = FailingHandler(failures=99, error=InvalidMessageError("bad"))

    result = await fixture.processor.process(MESSAGE, handler)

    assert result is ProcessResult.STOPPED
    assert fixture.store.stored == {}
