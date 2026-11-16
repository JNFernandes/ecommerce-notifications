"""Integration tests for the MongoDB dead-letter store."""

from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

from notifications.infrastructure.indexes import ensure_indexes
from notifications.infrastructure.repositories.dead_letters import (
    MongoDeadLetterStore,
    from_document,
)
from notifications.messaging.dead_letters import dead_letter_from, decode_payload
from notifications.messaging.messages import InvalidMessageError
from tests.support.fakes import consumed_message

type Document = dict[str, object]

FIRST = datetime(2026, 11, 16, 10, 0, 0, 1_000, tzinfo=UTC)
NOW = datetime(2026, 11, 16, 10, 0, 15, tzinfo=UTC)


async def test_should_store_every_field_of_the_dead_letter(
    database: AsyncDatabase[Document],
) -> None:
    dead_letter = dead_letter_from(
        consumed_message(value=b"not json", key=b"order-1", partition=1, offset=42),
        error=InvalidMessageError("value is not valid JSON"),
        attempts=1,
        first_failed_at=FIRST,
        now=NOW,
    )

    stored = await MongoDeadLetterStore(database).add(dead_letter)

    assert stored is True
    document = await database["inbound_dead_letters"].find_one({"offset": 42})
    assert document is not None
    assert from_document(document) == dead_letter
    assert document["payload"] == "not json"
    assert document["errorType"] == "InvalidMessageError"


async def test_should_not_store_a_second_dead_letter_for_the_same_message(
    database: AsyncDatabase[Document],
) -> None:
    await ensure_indexes(database)
    store = MongoDeadLetterStore(database)
    dead_letter = dead_letter_from(
        consumed_message(value=b"x", partition=0, offset=7),
        error=InvalidMessageError("bad"),
        attempts=1,
        first_failed_at=FIRST,
        now=NOW,
    )
    await store.add(dead_letter)

    stored_again = await store.add(dead_letter)

    assert stored_again is False
    assert await database["inbound_dead_letters"].count_documents({"offset": 7}) == 1


async def test_should_keep_a_binary_payload_recoverable(
    database: AsyncDatabase[Document],
) -> None:
    raw = b"\xff\xfe\x00binary"
    dead_letter = dead_letter_from(
        consumed_message(value=raw, offset=9),
        error=InvalidMessageError("value is not valid UTF-8"),
        attempts=1,
        first_failed_at=FIRST,
        now=NOW,
    )
    await MongoDeadLetterStore(database).add(dead_letter)

    document = await database["inbound_dead_letters"].find_one({"offset": 9})

    assert document is not None
    assert document["payloadEncoding"] == "base64"
    payload = document["payload"]
    assert isinstance(payload, str)
    assert decode_payload(payload, "base64") == raw
