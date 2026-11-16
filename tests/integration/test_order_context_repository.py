"""Integration tests for the MongoDB order context repository."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from bson.decimal128 import Decimal128
from pymongo.asynchronous.database import AsyncDatabase

from notifications.domain.order_context import OrderContext
from notifications.infrastructure.repositories.order_contexts import MongoOrderContextRepository

type Document = dict[str, object]

NOW = datetime(2026, 11, 16, 10, 0, 0, 120_000, tzinfo=UTC)


def _context(
    order_id: UUID, *, total: str = "129.90", now: datetime = NOW, customer: UUID | None = None
) -> OrderContext:
    return OrderContext.from_order_placed(
        order_id=order_id,
        customer_id=customer or UUID("c1c2c3c4-0000-4000-8000-0000000000cc"),
        total_amount=Decimal(total),
        now=now,
    )


async def test_should_insert_a_new_context_as_the_documented_document(
    database: AsyncDatabase[Document],
) -> None:
    order_id = uuid4()

    existing = await MongoOrderContextRepository(database).add_if_absent(_context(order_id))

    assert existing is None
    assert await database["order_context"].find_one({"_id": str(order_id)}) == {
        "_id": str(order_id),
        "customerId": "c1c2c3c4-0000-4000-8000-0000000000cc",
        "totalAmount": Decimal128("129.90"),
        "recordedAt": NOW,
    }


async def test_should_keep_the_first_context_and_return_it_when_one_exists(
    database: AsyncDatabase[Document],
) -> None:
    repository = MongoOrderContextRepository(database)
    order_id = uuid4()
    first = _context(order_id)
    await repository.add_if_absent(first)

    existing = await repository.add_if_absent(
        _context(order_id, total="1.00", now=NOW + timedelta(hours=1), customer=uuid4())
    )

    assert existing == first
    document = await database["order_context"].find_one({"_id": str(order_id)})
    assert document is not None
    assert document["totalAmount"] == Decimal128("129.90")
    assert document["recordedAt"] == NOW


async def test_should_keep_a_large_total_exact(database: AsyncDatabase[Document]) -> None:
    repository = MongoOrderContextRepository(database)
    order_id = uuid4()
    await repository.add_if_absent(_context(order_id, total="123456789012345678.123456789"))

    existing = await repository.add_if_absent(_context(order_id))

    assert existing is not None
    assert existing.total_amount == Decimal("123456789012345678.123456789")


async def test_should_store_one_context_when_two_writers_race_for_the_same_order(
    database: AsyncDatabase[Document],
) -> None:
    repository = MongoOrderContextRepository(database)
    order_id = uuid4()

    results = await asyncio.gather(
        *(repository.add_if_absent(_context(order_id)) for _ in range(5))
    )

    assert await database["order_context"].count_documents({"_id": str(order_id)}) == 1
    assert results.count(None) == 1


async def test_should_read_back_the_stored_context_exactly(
    database: AsyncDatabase[Document],
) -> None:
    repository = MongoOrderContextRepository(database)
    order_id = uuid4()
    stored = _context(order_id, total="123456789012345678.123456789")
    await repository.add_if_absent(stored)

    found = await repository.get(order_id)

    assert found == stored
    assert found is not None
    assert found.total_amount == Decimal("123456789012345678.123456789")
    assert found.recorded_at.utcoffset() is not None


async def test_should_find_no_context_for_an_unknown_order(
    database: AsyncDatabase[Document],
) -> None:
    assert await MongoOrderContextRepository(database).get(uuid4()) is None
