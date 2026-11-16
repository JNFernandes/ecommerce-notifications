"""Per-test MongoDB databases on the shared container."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

type Document = dict[str, object]


@pytest.fixture
async def database(mongodb_url: str) -> AsyncIterator[AsyncDatabase[Document]]:
    """Yield a fresh database (unique name) and drop it after the test."""
    client: AsyncMongoClient[Document] = AsyncMongoClient(
        mongodb_url, document_class=dict, tz_aware=True
    )
    name = f"it_{uuid4().hex}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        await client.close()
