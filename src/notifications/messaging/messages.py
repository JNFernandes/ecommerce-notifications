"""Consumed messages, the handler/processor contracts, and lossless JSON decoding."""

import json
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ConsumedMessage:
    """A Kafka record, copied out of the client library at the messaging boundary."""

    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    timestamp_ms: int


class InvalidMessageError(Exception):
    """The message cannot be processed because of its content; retrying cannot help."""

    def __init__(self, reason: str) -> None:
        """Create the error with a human-readable ``reason`` (stored in the dead letter)."""
        super().__init__(reason)
        self.reason = reason


class MessageHandler(Protocol):
    """Handles the messages of one topic: parse, validate, call one application service."""

    @property
    def topic(self) -> str:
        """The topic this handler consumes."""
        ...

    async def handle(self, message: ConsumedMessage) -> None:
        """Process one message. Raise ``InvalidMessageError`` for content that can never work."""
        ...


class ProcessResult(StrEnum):
    """What the consumer may do with the offset after processing a message."""

    HANDLED = "HANDLED"
    """The message was stored or dead-lettered: commit its offset."""
    STOPPED = "STOPPED"
    """A stop was requested before the message was handled: do not commit."""


class MessageProcessor(Protocol):
    """Runs a handler for one message and decides when the message counts as handled."""

    async def process(self, message: ConsumedMessage, handler: MessageHandler) -> ProcessResult:
        """Return only when the message is handled or a stop was requested."""
        ...


def decode_json_object(value: bytes | None) -> dict[str, object]:
    """Decode a message value into a JSON object, keeping every JSON float as an exact ``Decimal``.

    Raises:
        InvalidMessageError: if the value is missing, not UTF-8, not JSON, or not a JSON object.
    """
    if value is None:
        raise InvalidMessageError("message has no value")
    try:
        text = value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidMessageError(f"value is not valid UTF-8: {exc}") from exc
    try:
        parsed: object = json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise InvalidMessageError(f"value is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise InvalidMessageError(f"value is not a JSON object but {type(parsed).__name__}")
    return {str(key): item for key, item in parsed.items()}
