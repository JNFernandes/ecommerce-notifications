"""Containers shared by the component tests (MongoDB comes from ``tests/conftest.py``)."""

from collections.abc import Iterator

import pytest
from testcontainers.community.kafka import KafkaContainer

KAFKA_IMAGE = "confluentinc/cp-kafka:7.6.1"
"""Same image as the shared ecommerce-infra Kafka."""


@pytest.fixture(scope="session")
def kafka_container() -> Iterator[KafkaContainer]:
    """Start one Kafka broker for the whole component test session."""
    with KafkaContainer(KAFKA_IMAGE) as kafka:
        yield kafka


@pytest.fixture(scope="session")
def kafka_bootstrap(kafka_container: KafkaContainer) -> str:
    """Bootstrap server address of the test broker."""
    return kafka_container.get_bootstrap_server()
