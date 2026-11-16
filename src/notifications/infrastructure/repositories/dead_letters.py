"""MongoDB store for inbound dead letters (collection ``inbound_dead_letters``)."""

from datetime import datetime
from typing import cast

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from notifications.infrastructure.indexes import (
    DEAD_LETTERS_POSITION_INDEX,
    INBOUND_DEAD_LETTERS_COLLECTION,
    key_pattern,
)
from notifications.infrastructure.mongo import MongoDocument
from notifications.messaging.dead_letters import DeadLetter


def to_document(dead_letter: DeadLetter) -> MongoDocument:
    """Map a dead letter to its MongoDB document."""
    return {
        "topic": dead_letter.topic,
        "partition": dead_letter.partition,
        "offset": dead_letter.offset,
        "key": dead_letter.key,
        "keyEncoding": dead_letter.key_encoding,
        "payload": dead_letter.payload,
        "payloadEncoding": dead_letter.payload_encoding,
        "errorType": dead_letter.error_type,
        "errorMessage": dead_letter.error_message,
        "attempts": dead_letter.attempts,
        "firstFailedAt": dead_letter.first_failed_at,
        "deadLetteredAt": dead_letter.dead_lettered_at,
    }


def from_document(document: MongoDocument) -> DeadLetter:
    """Map a MongoDB document back to a dead letter."""
    return DeadLetter(
        topic=cast(str, document["topic"]),
        partition=cast(int, document["partition"]),
        offset=cast(int, document["offset"]),
        key=cast(str | None, document["key"]),
        key_encoding=cast(str | None, document["keyEncoding"]),
        payload=cast(str | None, document["payload"]),
        payload_encoding=cast(str | None, document["payloadEncoding"]),
        error_type=cast(str, document["errorType"]),
        error_message=cast(str, document["errorMessage"]),
        attempts=cast(int, document["attempts"]),
        first_failed_at=cast(datetime, document["firstFailedAt"]),
        dead_lettered_at=cast(datetime, document["deadLetteredAt"]),
    )


class MongoDeadLetterStore:
    """``DeadLetterStore`` backed by the ``inbound_dead_letters`` collection."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Use the ``inbound_dead_letters`` collection of ``database``."""
        self._collection = database[INBOUND_DEAD_LETTERS_COLLECTION]

    async def add(self, dead_letter: DeadLetter) -> bool:
        """Insert the dead letter.

        Returns:
            ``True`` if stored; ``False`` if this topic, partition and offset was already
            dead-lettered (a redelivery after a crash).

        Raises:
            PyMongoError: for any other database failure.
        """
        try:
            await self._collection.insert_one(to_document(dead_letter))
        except DuplicateKeyError as exc:
            details = exc.details or {}
            if details.get("keyPattern") == key_pattern(DEAD_LETTERS_POSITION_INDEX):
                return False
            raise
        return True
