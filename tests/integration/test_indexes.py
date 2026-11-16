"""Integration tests for the versioned index module."""

from pymongo.asynchronous.database import AsyncDatabase

from notifications.infrastructure.indexes import INDEX_SCHEMA_VERSION, INDEXES, ensure_indexes

type Document = dict[str, object]


async def _index_info(database: AsyncDatabase[Document]) -> dict[str, dict[str, object]]:
    info: dict[str, dict[str, object]] = {}
    for collection in {index.collection for index in INDEXES}:
        for name, details in (await database[collection].index_information()).items():
            info[name] = details
    return info


async def test_should_create_every_declared_index_with_its_keys_and_uniqueness(
    database: AsyncDatabase[Document],
) -> None:
    await ensure_indexes(database)

    info = await _index_info(database)

    assert INDEX_SCHEMA_VERSION == 3
    assert info["uq_notifications_source_event_id"]["key"] == [("sourceEventId", 1)]
    assert info["uq_notifications_source_event_id"]["unique"] is True
    assert info["ix_notifications_created_id"]["key"] == [("createdAt", -1), ("_id", -1)]
    for name, field in [
        ("ix_notifications_customer_created_id", "customerId"),
        ("ix_notifications_order_created_id", "orderId"),
        ("ix_notifications_type_created_id", "type"),
    ]:
        assert info[name]["key"] == [(field, 1), ("createdAt", -1), ("_id", -1)]
        assert "unique" not in info[name]
    assert "ix_notifications_customer_created" not in info
    assert info["uq_dead_letters_topic_partition_offset"]["key"] == [
        ("topic", 1),
        ("partition", 1),
        ("offset", 1),
    ]
    assert info["uq_dead_letters_topic_partition_offset"]["unique"] is True


async def test_should_be_safe_to_apply_the_indexes_on_every_start(
    database: AsyncDatabase[Document],
) -> None:
    await ensure_indexes(database)
    before = await _index_info(database)

    await ensure_indexes(database)

    assert await _index_info(database) == before


async def test_should_drop_the_index_replaced_in_version_3(
    database: AsyncDatabase[Document],
) -> None:
    await database["notifications"].create_index(
        [("customerId", 1), ("createdAt", -1)], name="ix_notifications_customer_created"
    )

    await ensure_indexes(database)

    names = set(await database["notifications"].index_information())
    assert "ix_notifications_customer_created" not in names
    assert "ix_notifications_customer_created_id" in names
