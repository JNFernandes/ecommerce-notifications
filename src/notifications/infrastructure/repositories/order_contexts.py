"""MongoDB repository for order contexts (collection ``order_context``).

The order id is the document ``_id``, so at most one context exists per order. ``$setOnInsert``
leaves an existing context untouched (first write wins), which makes the write idempotent
(ADR 0002).
"""

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from bson.decimal128 import Decimal128
from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from notifications.domain.order_context import OrderContext
from notifications.infrastructure.indexes import ORDER_CONTEXT_COLLECTION
from notifications.infrastructure.mongo import MongoDocument


def to_document(context: OrderContext) -> MongoDocument:
    """Map an order context to its MongoDB document (amount as ``Decimal128``)."""
    return {
        "_id": str(context.order_id),
        "customerId": str(context.customer_id),
        "totalAmount": Decimal128(str(context.total_amount)),
        "recordedAt": context.recorded_at,
    }


def from_document(document: MongoDocument) -> OrderContext:
    """Map a MongoDB document back to an order context."""
    total = cast(Decimal128, document["totalAmount"]).to_decimal()
    return OrderContext(
        order_id=UUID(cast(str, document["_id"])),
        customer_id=UUID(cast(str, document["customerId"])),
        total_amount=Decimal(total),
        recorded_at=cast(datetime, document["recordedAt"]),
    )


class MongoOrderContextRepository:
    """``OrderContextRepository`` backed by the ``order_context`` collection."""

    def __init__(self, database: AsyncDatabase[MongoDocument]) -> None:
        """Use the ``order_context`` collection of ``database``."""
        self._collection = database[ORDER_CONTEXT_COLLECTION]

    async def add_if_absent(self, context: OrderContext) -> OrderContext | None:
        """Insert ``context`` unless its order already has one.

        Returns:
            ``None`` if inserted, otherwise the existing context, unchanged.
        """
        try:
            return await self._upsert(context)
        except DuplicateKeyError:
            # Two concurrent upserts of the same _id: one inserts, the other fails with a
            # duplicate key. Retrying once finds the inserted document (MongoDB upsert pattern).
            return await self._upsert(context)

    async def get(self, order_id: UUID) -> OrderContext | None:
        """Return the context recorded for ``order_id``, or ``None``. Read-only."""
        document = await self._collection.find_one({"_id": str(order_id)})
        return None if document is None else from_document(document)

    async def _upsert(self, context: OrderContext) -> OrderContext | None:
        document = to_document(context)
        order_id = document.pop("_id")
        existing = await self._collection.find_one_and_update(
            {"_id": order_id},
            {"$setOnInsert": document},
            upsert=True,
            return_document=ReturnDocument.BEFORE,
        )
        return None if existing is None else from_document(existing)
