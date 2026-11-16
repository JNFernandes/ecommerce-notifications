"""Component tests: order confirmations and cancellations from a real Kafka into a real MongoDB."""

import logging
from collections.abc import AsyncIterator

import pytest

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import ORDER_CONFIRMED_TOPIC, order_status_payload, to_json_bytes
from tests.support.kafka import create_order_topics, produce
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
