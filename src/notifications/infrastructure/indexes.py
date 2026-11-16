"""Versioned MongoDB index definitions, applied at startup.

This module is the only place where indexes are declared (Technology Stack: no ad-hoc scripts).
To change an index, add or edit an entry in ``INDEXES`` and bump ``INDEX_SCHEMA_VERSION`` in the
same change. ``create_index`` is idempotent, so applying the definitions on every start is safe.
"""

import logging
from dataclasses import dataclass

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1
"""Version of the index definitions below. Bump it whenever ``INDEXES`` changes."""

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


INDEXES: tuple[IndexDefinition, ...] = ()
"""All indexes of the service. Empty until the first user story adds collections."""


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
