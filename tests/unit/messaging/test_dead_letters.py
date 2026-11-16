"""Tests for building dead letters from consumed messages."""

from datetime import UTC, datetime

import pytest

from notifications.messaging.dead_letters import dead_letter_from, decode_payload
from notifications.messaging.messages import InvalidMessageError
from tests.support.fakes import consumed_message

FIRST = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 16, 10, 0, 15, tzinfo=UTC)


def test_should_keep_a_utf8_payload_and_key_as_text_with_their_context() -> None:
    message = consumed_message(value=b'{"a": 1}', key=b"order-1", partition=2, offset=42)

    dead_letter = dead_letter_from(
        message,
        error=InvalidMessageError("customerId: Field required"),
        attempts=1,
        first_failed_at=FIRST,
        now=NOW,
    )

    assert (dead_letter.topic, dead_letter.partition, dead_letter.offset) == (
        "orders.order-placed",
        2,
        42,
    )
    assert (dead_letter.payload, dead_letter.payload_encoding) == ('{"a": 1}', "utf-8")
    assert (dead_letter.key, dead_letter.key_encoding) == ("order-1", "utf-8")
    assert dead_letter.error_type == "InvalidMessageError"
    assert dead_letter.error_message == "customerId: Field required"
    assert (dead_letter.attempts, dead_letter.first_failed_at, dead_letter.dead_lettered_at) == (
        1,
        FIRST,
        NOW,
    )


def test_should_store_a_payload_that_is_not_text_as_base64_without_losing_it() -> None:
    raw = b"\xff\xfe\x00binary"

    dead_letter = dead_letter_from(
        consumed_message(value=raw),
        error=InvalidMessageError("x"),
        attempts=1,
        first_failed_at=FIRST,
        now=NOW,
    )

    assert dead_letter.payload_encoding == "base64"
    assert decode_payload(dead_letter.payload, dead_letter.payload_encoding) == raw


def test_should_keep_a_missing_key_and_value_as_missing() -> None:
    dead_letter = dead_letter_from(
        consumed_message(value=None, key=None),
        error=InvalidMessageError("x"),
        attempts=1,
        first_failed_at=FIRST,
        now=NOW,
    )

    assert (dead_letter.key, dead_letter.key_encoding) == (None, None)
    assert (dead_letter.payload, dead_letter.payload_encoding) == (None, None)


def test_should_record_the_class_and_text_of_an_unexpected_error() -> None:
    dead_letter = dead_letter_from(
        consumed_message(),
        error=ConnectionError("mongo down"),
        attempts=5,
        first_failed_at=FIRST,
        now=NOW,
    )

    assert (dead_letter.error_type, dead_letter.error_message) == ("ConnectionError", "mongo down")
    assert dead_letter.attempts == 5


def test_should_reject_a_corrupt_base64_payload() -> None:
    with pytest.raises(ValueError, match="base64"):
        decode_payload("***", "base64")


def test_should_decode_a_text_payload_back_to_bytes() -> None:
    assert decode_payload("{}", "utf-8") == b"{}"
    assert decode_payload(None, None) is None
