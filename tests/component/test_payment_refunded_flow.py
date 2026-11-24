"""Component tests: payment refunds from a real Kafka into a real MongoDB."""

import logging
from collections.abc import AsyncIterator

import pytest

from tests.support.app import (
    RunningApp,
    assert_readable_over_http,
    component_settings,
    place_order,
    run_app,
)
from tests.support.events import (
    MISSING,
    ORDER_CANCELLED_TOPIC,
    ORDER_CONFIRMED_TOPIC,
    PAYMENT_REFUNDED_TOPIC,
    order_status_payload,
    payment_refunded_payload,
    to_json_bytes,
)
from tests.support.kafka import committed_offset, create_consumed_topics, produce
from tests.support.polling import eventually

pytestmark = pytest.mark.component

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


async def test_should_record_the_refund_for_the_customer_of_the_placed_order(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    placed = await place_order(app, kafka_bootstrap)
    refund = payment_refunded_payload(orderId=placed["aggregateId"])

    await produce(kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, to_json_bytes(refund))

    [notification] = await _notifications_for(app, refund["eventId"])
    await assert_readable_over_http(app, notification)
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


async def test_should_record_one_notification_when_a_refund_is_delivered_twice(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    placed = await place_order(app, kafka_bootstrap)
    refund = payment_refunded_payload(orderId=placed["aggregateId"])
    value, key = to_json_bytes(refund), str(refund["aggregateId"]).encode()

    _, first_offset = await produce(kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, value, key=key)
    partition, second_offset = await produce(
        kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, value, key=key
    )

    await eventually(
        lambda: committed_offset(
            kafka_bootstrap, app.settings.kafka_group_id, PAYMENT_REFUNDED_TOPIC, partition
        ),
        lambda committed: committed >= second_offset + 1,
        description="both deliveries committed",
    )
    notifications = (
        await app.database["notifications"].find({"sourceEventId": refund["eventId"]}).to_list()
    )
    assert len(notifications) == 1
    await assert_readable_over_http(app, notifications[0])
    assert len(_send_lines(caplog, notifications[0]["_id"])) == 1
    assert (
        await app.database["inbound_dead_letters"].count_documents(
            {
                "topic": PAYMENT_REFUNDED_TOPIC,
                "partition": partition,
                "offset": {"$in": [first_offset, second_offset]},
            }
        )
        == 0
    )


async def test_should_dead_letter_an_invalid_refund_and_process_the_next_one(
    fast_retry_app: RunningApp, kafka_bootstrap: str
) -> None:
    app, key = fast_retry_app, b"same-partition"
    placed = await place_order(app, kafka_bootstrap)
    invalid = payment_refunded_payload(orderId=placed["aggregateId"], currency=MISSING)
    valid = payment_refunded_payload(orderId=placed["aggregateId"])

    partition, invalid_offset = await produce(
        kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, to_json_bytes(invalid), key=key
    )
    await produce(kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, to_json_bytes(valid), key=key)

    [recorded] = await _notifications_for(app, valid["eventId"])
    await assert_readable_over_http(app, recorded)
    dead_letter = await app.database["inbound_dead_letters"].find_one(
        {"topic": PAYMENT_REFUNDED_TOPIC, "partition": partition, "offset": invalid_offset}
    )
    assert dead_letter is not None
    assert dead_letter["attempts"] == 1
    assert "currency: Field required" in str(dead_letter["errorMessage"])


async def test_should_serve_all_four_topics_for_one_order_with_one_consumer(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    placed = await place_order(app, kafka_bootstrap)
    order_id, customer_id = placed["aggregateId"], placed["customerId"]
    confirmed = order_status_payload(aggregateId=order_id, customerId=customer_id)
    cancelled = order_status_payload(aggregateId=order_id, customerId=customer_id)
    refund = payment_refunded_payload(orderId=order_id)

    await produce(kafka_bootstrap, ORDER_CONFIRMED_TOPIC, to_json_bytes(confirmed))
    await produce(kafka_bootstrap, ORDER_CANCELLED_TOPIC, to_json_bytes(cancelled))
    await produce(kafka_bootstrap, PAYMENT_REFUNDED_TOPIC, to_json_bytes(refund))

    found = await eventually(
        lambda: app.database["notifications"].find({"orderId": order_id}).to_list(),
        lambda documents: len(documents) == 4,
        description="the four notifications of the order",
    )
    assert sorted(str(d["type"]) for d in found) == [
        "ORDER_CANCELLED",
        "ORDER_CONFIRMED",
        "ORDER_RECEIVED",
        "PAYMENT_REFUNDED",
    ]
    assert {d["customerId"] for d in found} == {customer_id}
    for stored in found:
        await assert_readable_over_http(app, stored)
    readiness = await app.readiness()
    assert readiness["kafka"] == "CONNECTED"
