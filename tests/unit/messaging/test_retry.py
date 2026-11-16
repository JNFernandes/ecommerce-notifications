"""Tests for waiting between attempts."""

import asyncio
import time

import pytest

from notifications.messaging.retry import RetryPolicy, StopAwareSleeper


async def test_should_wait_the_full_delay_when_no_stop_is_requested() -> None:
    sleeper = StopAwareSleeper(asyncio.Event())

    assert await sleeper.sleep(0.01) is False


async def test_should_return_at_once_when_a_stop_was_requested() -> None:
    stop = asyncio.Event()
    stop.set()
    started = time.monotonic()

    stopped = await StopAwareSleeper(stop).sleep(30)

    assert stopped is True
    assert time.monotonic() - started < 1


def test_should_double_the_pause_with_the_default_policy() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=1.0, multiplier=2.0, max_delay=10.0)

    assert [policy.delay_before_attempt(n) for n in range(2, 6)] == [1.0, 2.0, 4.0, 8.0]


def test_should_cap_the_pause_at_the_maximum_delay() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=1, multiplier=2, max_delay=3)

    assert [policy.delay_before_attempt(n) for n in range(2, 6)] == [1.0, 2.0, 3.0, 3.0]


def test_should_keep_a_constant_pause_with_multiplier_one() -> None:
    policy = RetryPolicy(max_attempts=4, base_delay=0.5, multiplier=1, max_delay=10)

    assert [policy.delay_before_attempt(n) for n in range(2, 5)] == [0.5, 0.5, 0.5]


def test_should_refuse_a_pause_before_the_first_attempt() -> None:
    policy = RetryPolicy(max_attempts=5, base_delay=1, multiplier=2, max_delay=10)

    with pytest.raises(ValueError, match="no pause"):
        policy.delay_before_attempt(1)
