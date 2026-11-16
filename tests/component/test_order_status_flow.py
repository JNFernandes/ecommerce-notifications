"""Component tests: order confirmations and cancellations from a real Kafka into a real MongoDB."""

import logging
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import (
    MISSING,
    ORDER_CANCELLED_TOPIC,
    ORDER_CONFIRMED_TOPIC,
    order_placed_payload,
    order_status_payload,
    to_json_bytes,
)
from tests.support.kafka import committed_offset, create_consumed_topics, produce
from tests.support.polling import eventually

pytestmark = pytest.mark.component

ORDER_PLACED_TOPIC = "orders.order-placed"

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


@pytest.fixture
async def fast_retry_app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application with fast retries, so a failing message cannot slow the test down."""
    await create_consumed_topics(kafka_bootstrap)
    settings = component_settings(
        mongodb_url,
        kafka_bootstrap,
        consumer_retry_base_delay_seconds=0.05,
        consumer_retry_max_delay_seconds=0.1,
    )
    async with run_app(settings) as running:
        yield running


async def test_should_dead_letter_invalid_cancellations_and_process_the_next_valid_one(
    fast_retry_app: RunningApp, kafka_bootstrap: str
) -> None:
    app, key = fast_retry_app, b"same-partition"
    valid = order_status_payload(reason="stock rejected: insufficient stock")
    partition, wrong_reason_offset = await produce(
        kafka_bootstrap,
        ORDER_CANCELLED_TOPIC,
        to_json_bytes(order_status_payload(reason=42)),
        key=key,
    )
    _, missing_customer_offset = await produce(
        kafka_bootstrap,
        ORDER_CANCELLED_TOPIC,
        to_json_bytes(order_status_payload(customerId=MISSING)),
        key=key,
    )
    _, valid_offset = await produce(
        kafka_bootstrap, ORDER_CANCELLED_TOPIC, to_json_bytes(valid), key=key
    )

    await eventually(
        lambda: committed_offset(
            kafka_bootstrap, app.settings.kafka_group_id, ORDER_CANCELLED_TOPIC, partition
        ),
        lambda committed: committed == valid_offset + 1,
        description="all three cancellations committed",
    )

    dead_letters = await (
        app.database["inbound_dead_letters"]
        .find(
            {
                "topic": ORDER_CANCELLED_TOPIC,
                "partition": partition,
                "offset": {"$in": [wrong_reason_offset, missing_customer_offset, valid_offset]},
            },
            {"_id": 0},
        )
        .sort("offset")
        .to_list()
    )
    assert [d["offset"] for d in dead_letters] == [wrong_reason_offset, missing_customer_offset]
    assert all(d["attempts"] == 1 and d["errorType"] == "InvalidMessageError" for d in dead_letters)
    assert "reason: Value error, must be a JSON string or null" in str(
        dead_letters[0]["errorMessage"]
    )
    assert "customerId: Field required" in str(dead_letters[1]["errorMessage"])
    assert (
        await app.database["notifications"].count_documents({"sourceEventId": valid["eventId"]})
        == 1
    )


async def test_should_serve_all_order_topics_with_one_consumer_and_no_placement_needed(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    order_id = str(uuid4())
    confirmed = order_status_payload(aggregateId=order_id)
    cancelled = order_status_payload(aggregateId=order_id, reason="payment failed")
    placed = order_placed_payload()

    await produce(kafka_bootstrap, ORDER_CONFIRMED_TOPIC, to_json_bytes(confirmed))
    await produce(kafka_bootstrap, ORDER_CANCELLED_TOPIC, to_json_bytes(cancelled))
    await produce(kafka_bootstrap, ORDER_PLACED_TOPIC, to_json_bytes(placed))

    [confirmation] = await _notifications_for(app, confirmed["eventId"])
    [cancellation] = await _notifications_for(app, cancelled["eventId"])
    [received] = await _notifications_for(app, placed["eventId"])
    assert (confirmation["type"], cancellation["type"], received["type"]) == (
        "ORDER_CONFIRMED",
        "ORDER_CANCELLED",
        "ORDER_RECEIVED",
    )
    assert confirmation["orderId"] == cancellation["orderId"] == order_id
    assert await app.database["order_context"].count_documents({"_id": order_id}) == 0
    readiness = await app.readiness()
    assert readiness["kafka"] == "CONNECTED"
    assert set(readiness) == {"status", "mongodb", "kafka"}
