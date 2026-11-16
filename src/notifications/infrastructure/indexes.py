"""Versioned MongoDB index definitions, applied at startup.

This module is the only place where indexes are declared (Technology Stack: no ad-hoc scripts).
To change an index, add or edit an entry in ``INDEXES`` and bump ``INDEX_SCHEMA_VERSION`` in the
same change. ``create_index`` is idempotent, so applying the definitions on every start is safe.
"""

import logging
from dataclasses import dataclass

from pymongo import ASCENDING, DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 2
"""Version of the index definitions below. Bump it whenever ``INDEXES`` changes.

History:
    1: no indexes (bootstrap).
    2: US-01. Unique source event and customer/newest-first on ``notifications``; unique
       topic/partition/offset on ``inbound_dead_letters``. ``order_context`` uses the order id
       as ``_id``, which is unique by definition.
"""

NOTIFICATIONS_COLLECTION = "notifications"
ORDER_CONTEXT_COLLECTION = "order_context"
INBOUND_DEAD_LETTERS_COLLECTION = "inbound_dead_letters"


@dataclass(frozen=True, slots=True)
class IndexDefinition:
    """One index on one collection."""

    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    """Field names with ``pymongo.ASCENDING`` or ``pymongo.DESCENDING``."""
    unique: bool = False


NOTIFICATIONS_SOURCE_EVENT_INDEX = IndexDefinition(
    collection=NOTIFICATIONS_COLLECTION,
    name="uq_notifications_source_event_id",
    keys=(("sourceEventId", ASCENDING),),
    unique=True,
)
"""At most one notification per source event (Principle IV, ADR 0002)."""

NOTIFICATIONS_CUSTOMER_CREATED_INDEX = IndexDefinition(
    collection=NOTIFICATIONS_COLLECTION,
    name="ix_notifications_customer_created",
    keys=(("customerId", ASCENDING), ("createdAt", DESCENDING)),
)
"""A customer's notifications, newest first (read API, US-04)."""

DEAD_LETTERS_POSITION_INDEX = IndexDefinition(
    collection=INBOUND_DEAD_LETTERS_COLLECTION,
    name="uq_dead_letters_topic_partition_offset",
    keys=(("topic", ASCENDING), ("partition", ASCENDING), ("offset", ASCENDING)),
    unique=True,
)
"""At most one dead letter per consumed message (FR-017)."""

INDEXES: tuple[IndexDefinition, ...] = (
    NOTIFICATIONS_SOURCE_EVENT_INDEX,
    NOTIFICATIONS_CUSTOMER_CREATED_INDEX,
    DEAD_LETTERS_POSITION_INDEX,
)
"""All indexes of the service, version ``INDEX_SCHEMA_VERSION``."""


def key_pattern(index: IndexDefinition) -> dict[str, int]:
    """Return the index keys in the form MongoDB reports as ``keyPattern`` in duplicate errors."""
    return dict(index.keys)


async def ensure_indexes(
    database: AsyncDatabase[dict[str, object]],
    indexes: tuple[IndexDefinition, ...] = INDEXES,
) -> None:
    """Create every declared index that does not exist yet."""
    for index in indexes:
        await database[index.collection].create_index(
            list(index.keys), name=index.name, unique=index.unique
        )
    logger.info(
        "MongoDB indexes applied (schema version %d, %d indexes)",
        INDEX_SCHEMA_VERSION,
        len(indexes),
    )
