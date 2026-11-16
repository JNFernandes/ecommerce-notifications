"""Component tests: ``orders.order-placed`` consumed from a real Kafka into a real MongoDB."""

import logging
from collections.abc import AsyncIterator
from datetime import datetime

import pytest
from bson.decimal128 import Decimal128

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import order_placed_payload, to_json_bytes
from tests.support.kafka import committed_offset, create_consumed_topics, produce
from tests.support.polling import eventually

pytestmark = pytest.mark.component

TOPIC = "orders.order-placed"
type Document = dict[str, object]


@pytest.fixture
async def app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application, connected to the test broker and a fresh database."""
    await create_consumed_topics(kafka_bootstrap)
    async with run_app(component_settings(mongodb_url, kafka_bootstrap)) as running:
        yield running


def _send_lines(caplog: pytest.LogCaptureFixture, notification_id: object) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "notifications.send"
        and f"notification={notification_id}" in record.getMessage()
    ]


async def test_should_record_an_order_received_notification_for_a_placed_order(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    payload = order_placed_payload()
    order_id = str(payload["aggregateId"])

    await produce(kafka_bootstrap, TOPIC, to_json_bytes(payload), key=order_id.encode())

    notifications = await eventually(
        lambda: app.database["notifications"].find({"orderId": order_id}).to_list(),
        lambda found: len(found) == 1,
        description="the order received notification",
    )
    [notification] = notifications
    assert notification["customerId"] == payload["customerId"]
    assert notification["sourceEventId"] == payload["eventId"]
    assert (notification["type"], notification["channel"], notification["status"]) == (
        "ORDER_RECEIVED",
        "email",
        "SENT",
    )
    assert notification["message"] == f"Your order {order_id} for 129.90 was received."
    assert len(_send_lines(caplog, notification["_id"])) == 1
    assert (await app.readiness())["kafka"] == "CONNECTED"
    context = await app.database["order_context"].find_one({"_id": order_id})
    assert context is not None
    assert context["customerId"] == payload["customerId"]
    assert context["totalAmount"] == Decimal128("129.90")
    recorded_at, created_at = context["recordedAt"], notification["createdAt"]
    assert isinstance(recorded_at, datetime)
    assert isinstance(created_at, datetime)
    assert recorded_at.utcoffset() is not None
    assert recorded_at <= created_at


async def test_should_record_one_notification_when_the_same_event_is_delivered_twice(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    payload = order_placed_payload()
    value, key = to_json_bytes(payload), str(payload["aggregateId"]).encode()

    _, first_offset = await produce(kafka_bootstrap, TOPIC, value, key=key)
    partition, second_offset = await produce(kafka_bootstrap, TOPIC, value, key=key)

    await eventually(
        lambda: committed_offset(kafka_bootstrap, app.settings.kafka_group_id, TOPIC, partition),
        lambda committed: committed == second_offset + 1,
        description="both deliveries committed",
    )
    notifications = (
        await app.database["notifications"].find({"sourceEventId": payload["eventId"]}).to_list()
    )
    assert len(notifications) == 1
    assert len(_send_lines(caplog, notifications[0]["_id"])) == 1
    # The topic is shared by all component tests: only this test's offsets are relevant.
    assert (
        await app.database["inbound_dead_letters"].count_documents(
            {"partition": partition, "offset": {"$in": [first_offset, second_offset]}}
        )
        == 0
    )
