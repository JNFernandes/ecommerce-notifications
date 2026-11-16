"""Component tests: payment refunds from a real Kafka into a real MongoDB."""

import logging
from collections.abc import AsyncIterator

import pytest

from tests.support.app import RunningApp, component_settings, run_app
from tests.support.events import (
    PAYMENT_REFUNDED_TOPIC,
    order_placed_payload,
    payment_refunded_payload,
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


async def _place_order(app: RunningApp, kafka_bootstrap: str) -> dict[str, object]:
    """Produce a placement and wait until its order context exists (refunds need it)."""
    placed = order_placed_payload()
    await produce(kafka_bootstrap, ORDER_PLACED_TOPIC, to_json_bytes(placed))
    await eventually(
        lambda: app.database["order_context"].count_documents({"_id": placed["aggregateId"]}),
        lambda count: count == 1,
        description="the order context of the placement",
    )
    return placed


async def _notifications_for(app: RunningApp, event_id: object) -> list[Document]:
    return await eventually(
        lambda: app.database["notifications"].find({"sourceEventId": event_id}).to_list(),
        lambda found: len(found) == 1,
        description=f"the notification of event {event_id}",
    )


async def test_should_record_the_refund_for_the_customer_of_the_placed_order(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    placed = await _place_order(app, kafka_bootstrap)
    refund = payment_refunded_payload(orderId=placed["aggregateId"])

    await produce(kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, to_json_bytes(refund))

    [notification] = await _notifications_for(app, refund["eventId"])
    assert notification["type"] == "PAYMENT_REFUNDED"
    assert notification["customerId"] == placed["customerId"]
    assert notification["orderId"] == placed["aggregateId"]
    assert notification["message"] == (
        f"Your payment of 129.90 EUR for order {placed['aggregateId']} was refunded."
    )
    assert len(_send_lines(caplog, notification["_id"])) == 1
    assert "type=PAYMENT_REFUNDED" in _send_lines(caplog, notification["_id"])[0]
    assert (
        await app.database["notifications"].count_documents({"orderId": refund["aggregateId"]}) == 0
    )


@pytest.fixture
async def fast_retry_app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application with three fast attempts per message."""
    await create_consumed_topics(kafka_bootstrap)
    settings = component_settings(
        mongodb_url,
        kafka_bootstrap,
        consumer_retry_max_attempts=3,
        consumer_retry_base_delay_seconds=0.05,
        consumer_retry_max_delay_seconds=0.1,
    )
    async with run_app(settings) as running:
        yield running


async def test_should_dead_letter_a_refund_whose_order_was_never_placed(
    fast_retry_app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="notifications.messaging")
    app, refund = fast_retry_app, payment_refunded_payload()

    partition, offset = await produce(
        kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, to_json_bytes(refund)
    )

    await eventually(
        lambda: committed_offset(
            kafka_bootstrap, app.settings.kafka_group_id, PAYMENT_REFUNDED_TOPIC, partition
        ),
        lambda committed: committed >= offset + 1,
        description="the refund committed after its dead letter",
    )
    dead_letter = await app.database["inbound_dead_letters"].find_one(
        {"topic": PAYMENT_REFUNDED_TOPIC, "partition": partition, "offset": offset}
    )
    assert dead_letter is not None
    assert dead_letter["errorType"] == "OrderContextNotFoundError"
    assert dead_letter["attempts"] == 3
    assert str(refund["orderId"]) in str(dead_letter["errorMessage"])
    assert (
        await app.database["notifications"].count_documents({"sourceEventId": refund["eventId"]})
        == 0
    )
    retries = [r.getMessage() for r in caplog.records if "retrying" in r.getMessage()]
    assert any(f"offset={offset} attempt=2/3" in message for message in retries)
