"""Component tests: invalid messages are dead-lettered without blocking the partition."""

from collections.abc import AsyncIterator

import pytest

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import MISSING, order_placed_payload, to_json_bytes
from tests.support.kafka import committed_offset, create_order_topics, produce
from tests.support.polling import eventually

pytestmark = pytest.mark.component

TOPIC = "orders.order-placed"


@pytest.fixture
async def app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application with fast retries, connected to the test broker."""
    await create_order_topics(kafka_bootstrap)
    settings = component_settings(
        mongodb_url,
        kafka_bootstrap,
        consumer_retry_base_delay_seconds=0.05,
        consumer_retry_max_delay_seconds=0.1,
    )
    async with run_app(settings) as running:
        yield running


async def test_should_dead_letter_invalid_messages_and_still_process_the_next_valid_one(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    key = b"same-partition"
    valid = order_placed_payload()
    partition, bad_json_offset = await produce(kafka_bootstrap, TOPIC, b"not json", key=key)
    _, missing_customer_offset = await produce(
        kafka_bootstrap, TOPIC, to_json_bytes(order_placed_payload(customerId=MISSING)), key=key
    )
    _, valid_offset = await produce(kafka_bootstrap, TOPIC, to_json_bytes(valid), key=key)

    await eventually(
        lambda: committed_offset(kafka_bootstrap, app.settings.kafka_group_id, TOPIC, partition),
        lambda committed: committed == valid_offset + 1,
        description="all three messages committed",
    )

    dead_letters = await (
        app.database["inbound_dead_letters"].find({}, {"_id": 0}).sort("offset").to_list()
    )
    assert [(d["partition"], d["offset"]) for d in dead_letters] == [
        (partition, bad_json_offset),
        (partition, missing_customer_offset),
    ]
    assert all(d["topic"] == TOPIC and d["attempts"] == 1 for d in dead_letters)
    assert all(d["errorType"] == "InvalidMessageError" for d in dead_letters)
    assert dead_letters[0]["payload"] == "not json"
    assert "not valid JSON" in str(dead_letters[0]["errorMessage"])
    assert "customerId: Field required" in str(dead_letters[1]["errorMessage"])
    assert (
        await app.database["notifications"].count_documents({"sourceEventId": valid["eventId"]})
        == 1
    )
