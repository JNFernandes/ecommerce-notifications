"""Component tests: order confirmations and cancellations from a real Kafka into a real MongoDB."""

import logging
from collections.abc import AsyncIterator

import pytest

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import (
    ORDER_CANCELLED_TOPIC,
    ORDER_CONFIRMED_TOPIC,
    order_status_payload,
    to_json_bytes,
)
from tests.support.kafka import committed_offset, create_order_topics, produce
from tests.support.polling import eventually

pytestmark = pytest.mark.component

type Document = dict[str, object]


@pytest.fixture
async def app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application, connected to the test broker and a fresh database."""
    await create_order_topics(kafka_bootstrap)
    async with run_app(component_settings(mongodb_url, kafka_bootstrap)) as running:
        yield running


def _send_lines(caplog: pytest.LogCaptureFixture, notification_id: object) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "notifications.send"
        and f"notification={notification_id}" in record.getMessage()
    ]


async def _notifications_for(app: RunningApp, event_id: object) -> list[Document]:
    return await eventually(
        lambda: app.database["notifications"].find({"sourceEventId": event_id}).to_list(),
        lambda found: len(found) == 1,
        description=f"the notification of event {event_id}",
    )


async def test_should_record_an_order_confirmed_notification_for_a_confirmation(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    payload = order_status_payload()
    order_id = str(payload["aggregateId"])

    await produce(kafka_bootstrap, ORDER_CONFIRMED_TOPIC, to_json_bytes(payload))

    [notification] = await _notifications_for(app, payload["eventId"])
    assert notification["type"] == "ORDER_CONFIRMED"
    assert (notification["channel"], notification["status"]) == ("email", "SENT")
    assert notification["customerId"] == payload["customerId"]
    assert notification["orderId"] == order_id
    assert notification["message"] == f"Your order {order_id} was confirmed."
    assert len(_send_lines(caplog, notification["_id"])) == 1
    assert "type=ORDER_CONFIRMED" in _send_lines(caplog, notification["_id"])[0]
    assert await app.database["order_context"].count_documents({"_id": order_id}) == 0


async def test_should_record_order_cancelled_notifications_with_and_without_a_reason(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    with_reason = order_status_payload(reason="payment failed: amount exceeds the threshold")
    without_reason = order_status_payload()

    await produce(kafka_bootstrap, ORDER_CANCELLED_TOPIC, to_json_bytes(with_reason))
    await produce(kafka_bootstrap, ORDER_CANCELLED_TOPIC, to_json_bytes(without_reason))

    [first] = await _notifications_for(app, with_reason["eventId"])
    [second] = await _notifications_for(app, without_reason["eventId"])
    assert first["type"] == second["type"] == "ORDER_CANCELLED"
    assert first["message"] == (
        f"Your order {with_reason['aggregateId']} was cancelled: "
        "payment failed: amount exceeds the threshold."
    )
    assert second["message"] == f"Your order {without_reason['aggregateId']} was cancelled."
    assert first["customerId"] == with_reason["customerId"]
    assert len(_send_lines(caplog, first["_id"])) == 1
    assert len(_send_lines(caplog, second["_id"])) == 1


async def test_should_record_one_notification_when_a_confirmation_is_delivered_twice(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    payload = order_status_payload()
    value, key = to_json_bytes(payload), str(payload["aggregateId"]).encode()

    _, first_offset = await produce(kafka_bootstrap, ORDER_CONFIRMED_TOPIC, value, key=key)
    partition, second_offset = await produce(kafka_bootstrap, ORDER_CONFIRMED_TOPIC, value, key=key)

    await eventually(
        lambda: committed_offset(
            kafka_bootstrap, app.settings.kafka_group_id, ORDER_CONFIRMED_TOPIC, partition
        ),
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
            {
                "topic": ORDER_CONFIRMED_TOPIC,
                "partition": partition,
                "offset": {"$in": [first_offset, second_offset]},
            }
        )
        == 0
    )
