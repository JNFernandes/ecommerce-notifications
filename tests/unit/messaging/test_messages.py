"""Tests for lossless JSON decoding of message values."""

from decimal import Decimal

import pytest

from notifications.messaging.messages import InvalidMessageError, decode_json_object


def test_should_decode_a_json_object() -> None:
    assert decode_json_object(b'{"a": "b", "n": 1}') == {"a": "b", "n": 1}


def test_should_keep_large_decimal_amounts_exact() -> None:
    result = decode_json_object(b'{"totalAmount": 123456789012345678.123456789}')

    assert result["totalAmount"] == Decimal("123456789012345678.123456789")


def test_should_keep_json_integers_as_integers() -> None:
    result = decode_json_object(b'{"version": 1}')

    assert type(result["version"]) is int


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (None, "no value"),
        (b"\xff\xfe", "UTF-8"),
        (b"not json", "not valid JSON"),
        (b"[]", "not a JSON object but list"),
        (b"null", "not a JSON object but NoneType"),
    ],
)
def test_should_reject_values_that_are_not_a_json_object(value: bytes | None, reason: str) -> None:
    with pytest.raises(InvalidMessageError, match=reason) as raised:
        decode_json_object(value)

    assert reason in raised.value.reason
