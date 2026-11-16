"""Kafka helpers for component tests: topics, producing, and simulated broker outages."""

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError
from testcontainers.community.kafka import KafkaContainer


async def create_topic(bootstrap: str, name: str, partitions: int = 3) -> None:
    """Create ``name`` like ``kafka-init`` does in ecommerce-infra; an existing topic is fine."""
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        await admin.create_topics(
            [NewTopic(name=name, num_partitions=partitions, replication_factor=1)]
        )
    except TopicAlreadyExistsError:
        pass
    finally:
        await admin.close()


async def produce(
    bootstrap: str, topic: str, value: bytes, key: bytes | None = None
) -> tuple[int, int]:
    """Produce one message, wait for the broker acknowledgement, return (partition, offset)."""
    producer = AIOKafkaProducer(bootstrap_servers=bootstrap)
    await producer.start()
    try:
        metadata = await producer.send_and_wait(topic, value=value, key=key)
    finally:
        await producer.stop()
    partition: int = metadata.partition
    offset: int = metadata.offset
    return partition, offset


async def committed_offset(bootstrap: str, group_id: str, topic: str, partition: int) -> int:
    """Return the committed offset of ``group_id`` for one partition (-1 when none)."""
    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        offsets = await admin.list_consumer_group_offsets(group_id)
    finally:
        await admin.close()
    for topic_partition, metadata in offsets.items():
        if topic_partition.topic == topic and topic_partition.partition == partition:
            offset: int = metadata.offset
            return offset
    return -1


def pause_kafka(container: KafkaContainer) -> None:
    """Freeze the broker so it stops answering, keeping its address (``docker pause``)."""
    container.get_wrapped_container().pause()


def unpause_kafka(container: KafkaContainer) -> None:
    """Resume a paused broker."""
    container.get_wrapped_container().unpause()


async def create_topics(bootstrap: str, names: list[str], partitions: int = 3) -> None:
    """Create every topic in ``names`` (existing topics are fine)."""
    for name in names:
        await create_topic(bootstrap, name, partitions)


ORDER_TOPICS = ["orders.order-placed", "orders.order-confirmed", "orders.order-cancelled"]
"""Every topic the consumer subscribes to with default settings, like ``kafka-init`` creates."""


async def create_order_topics(bootstrap: str) -> None:
    """Create all consumed order topics, as ecommerce-infra does before the services start."""
    await create_topics(bootstrap, ORDER_TOPICS)
