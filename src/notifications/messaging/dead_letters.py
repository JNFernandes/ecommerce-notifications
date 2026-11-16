"""Inbound dead letters: consumed messages that could not be processed (FR-014 to FR-017)."""

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from notifications.messaging.messages import ConsumedMessage, InvalidMessageError

UTF8 = "utf-8"
BASE64 = "base64"


@dataclass(frozen=True, slots=True)
class DeadLetter:
    """A consumed message set aside with the reason it could not be processed.

    At most one dead letter exists per topic, partition and offset.
    """

    topic: str
    partition: int
    offset: int
    key: str | None
    key_encoding: str | None
    payload: str | None
    payload_encoding: str | None
    error_type: str
    error_message: str
    attempts: int
    first_failed_at: datetime
    dead_lettered_at: datetime


class DeadLetterStore(Protocol):
    """Stores dead letters; storing the same message twice is not an error."""

    async def add(self, dead_letter: DeadLetter) -> bool:
        """Store ``dead_letter``.

        Returns:
            ``True`` if stored, ``False`` if a dead letter for the same topic, partition and
            offset already exists.
        """
        ...


def _encode(raw: bytes | None) -> tuple[str | None, str | None]:
    """Return ``raw`` as UTF-8 text, or base64 when it is not valid UTF-8, with its encoding."""
    if raw is None:
        return None, None
    try:
        return raw.decode(UTF8), UTF8
    except UnicodeDecodeError:
        return base64.b64encode(raw).decode("ascii"), BASE64


def decode_payload(text: str | None, encoding: str | None) -> bytes | None:
    """Inverse of the payload encoding: the original bytes of a stored key or payload."""
    if text is None:
        return None
    if encoding == BASE64:
        try:
            return base64.b64decode(text, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"invalid base64 payload: {exc}") from exc
    return text.encode(UTF8)


def dead_letter_from(
    message: ConsumedMessage,
    *,
    error: Exception,
    attempts: int,
    first_failed_at: datetime,
    now: datetime,
) -> DeadLetter:
    """Build the dead letter of ``message`` after it failed with ``error``."""
    key, key_encoding = _encode(message.key)
    payload, payload_encoding = _encode(message.value)
    reason = error.reason if isinstance(error, InvalidMessageError) else str(error)
    return DeadLetter(
        topic=message.topic,
        partition=message.partition,
        offset=message.offset,
        key=key,
        key_encoding=key_encoding,
        payload=payload,
        payload_encoding=payload_encoding,
        error_type=type(error).__name__,
        error_message=reason,
        attempts=attempts,
        first_failed_at=first_failed_at,
        dead_lettered_at=now,
    )
