"""Containers shared by the component tests."""

from collections.abc import Iterator

import pytest
from testcontainers.community.mongodb import MongoDbContainer

MONGO_IMAGE = "mongo:7"
"""Same major version as the shared ecommerce-infra MongoDB."""


@pytest.fixture(scope="session")
def mongodb_url() -> Iterator[str]:
    with MongoDbContainer(MONGO_IMAGE) as mongo:
        yield mongo.get_connection_url()
