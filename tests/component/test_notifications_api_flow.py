"""Component tests: notifications consumed from a real Kafka, read back through the HTTP API."""

from collections.abc import AsyncIterator

import pytest
from testcontainers.community.kafka import KafkaContainer

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import order_placed_payload, to_json_bytes
from tests.support.kafka import create_consumed_topics, pause_kafka, produce, unpause_kafka
from tests.support.polling import eventually

pytestmark = pytest.mark.component

ORDER_PLACED_TOPIC = "orders.order-placed"


@pytest.fixture
async def app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application, connected to the test broker and a fresh database."""
    await create_consumed_topics(kafka_bootstrap)
    async with run_app(component_settings(mongodb_url, kafka_bootstrap)) as running:
        yield running


async def _page_for(app: RunningApp, order_id: object) -> dict[str, object]:
    body: dict[str, object] = (
        await app.http.get("/notifications", params={"orderId": str(order_id)})
    ).json()
    return body


async def test_should_read_a_consumed_notification_through_the_api(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    placed = order_placed_payload()
    await produce(kafka_bootstrap, ORDER_PLACED_TOPIC, to_json_bytes(placed))

    page = await eventually(
        lambda: _page_for(app, placed["aggregateId"]),
        lambda body: body["total"] == 1,
        description="the notification listed by order",
    )
    items = page["items"]
    assert isinstance(items, list)
    [listed] = items
    assert listed["type"] == "ORDER_RECEIVED"
    assert listed["customerId"] == placed["customerId"]
    assert listed["sourceEventId"] == placed["eventId"]
    fetched = await app.http.get(f"/notifications/{listed['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == listed


async def test_should_keep_answering_reads_while_kafka_is_down(
    app: RunningApp, kafka_bootstrap: str, kafka_container: KafkaContainer
) -> None:
    placed = order_placed_payload()
    await produce(kafka_bootstrap, ORDER_PLACED_TOPIC, to_json_bytes(placed))
    before = await eventually(
        lambda: _page_for(app, placed["aggregateId"]),
        lambda body: body["total"] == 1,
        description="the notification listed by order",
    )

    pause_kafka(kafka_container)
    try:
        during = await app.http.get(
            "/notifications", params={"orderId": str(placed["aggregateId"])}
        )
    finally:
        unpause_kafka(kafka_container)

    assert during.status_code == 200
    assert during.json() == before
