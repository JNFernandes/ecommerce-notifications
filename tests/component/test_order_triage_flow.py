"""Component tests: AI order failure explanations from a real Kafka into a real MongoDB.

``ai.order-triage-completed`` has no customer: it is addressed through the order context of the
placement. The explanation is untrusted text, cleaned before it is stored and served.
"""

import logging
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from tests.support.app import (
    TOKENS,
    RunningApp,
    admin_headers,
    assert_readable_over_http,
    bearer,
    component_settings,
    place_order,
    run_app,
)
from tests.support.events import ORDER_TRIAGE_COMPLETED_TOPIC, order_triage_payload, to_json_bytes
from tests.support.kafka import committed_offset, create_consumed_topics, produce
from tests.support.polling import eventually

pytestmark = pytest.mark.component

type Document = dict[str, object]


@pytest.fixture
async def app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application with three fast attempts per message and a fresh database."""
    await create_consumed_topics(kafka_bootstrap)
    settings = component_settings(mongodb_url, kafka_bootstrap, consumer_retry_max_attempts=3)
    async with run_app(settings) as running:
        yield running


async def _explanation_of(app: RunningApp, event_id: object) -> Document:
    [document] = await eventually(
        lambda: app.database["notifications"].find({"sourceEventId": event_id}).to_list(),
        lambda found: len(found) == 1,
        description=f"the explanation of event {event_id}",
    )
    return document


async def _committed_past(
    app: RunningApp, kafka_bootstrap: str, partition: int, offset: int
) -> None:
    await eventually(
        lambda: committed_offset(
            kafka_bootstrap, app.settings.kafka_group_id, ORDER_TRIAGE_COMPLETED_TOPIC, partition
        ),
        lambda committed: committed >= offset + 1,
        description=f"offset {offset} committed",
    )


async def _dead_letter(app: RunningApp, partition: int, offset: int) -> Document:
    document = await app.database["inbound_dead_letters"].find_one(
        {"topic": ORDER_TRIAGE_COMPLETED_TOPIC, "partition": partition, "offset": offset}
    )
    assert document is not None
    return document


async def test_should_record_the_explanation_for_the_customer_of_the_placed_order(
    app: RunningApp, kafka_bootstrap: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="notifications.send")
    placed = await place_order(app, kafka_bootstrap)
    triage = order_triage_payload(order_id=placed["aggregateId"])

    await produce(kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, to_json_bytes(triage))

    notification = await _explanation_of(app, triage["eventId"])
    await assert_readable_over_http(app, notification)
    assert notification["type"] == "ORDER_FAILURE_EXPLANATION"
    assert notification["customerId"] == placed["customerId"]
    assert notification["orderId"] == placed["aggregateId"]
    assert notification["message"] == triage["explanation"]
    body = (
        await app.http.get(f"/notifications/{notification['_id']}", headers=admin_headers())
    ).json()
    assert body["suggestedAction"] == "RESTOCK"
    assert body["source"] == "LLM"
    assert body["confidence"] == 0.92
    assert body["promptVersion"] == "triage.v1"
    sends = [r.getMessage() for r in caplog.records if r.name == "notifications.send"]
    assert sum(f"notification={notification['_id']}" in line for line in sends) == 1


async def test_should_record_one_explanation_when_the_event_is_delivered_twice(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    placed = await place_order(app, kafka_bootstrap)
    triage = order_triage_payload(order_id=placed["aggregateId"])
    value, key = to_json_bytes(triage), str(placed["aggregateId"]).encode()

    await produce(kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, value, key=key)
    partition, second = await produce(kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, value, key=key)

    await _committed_past(app, kafka_bootstrap, partition, second)
    documents = (
        await app.database["notifications"].find({"sourceEventId": triage["eventId"]}).to_list()
    )
    assert len(documents) == 1
    await assert_readable_over_http(app, documents[0])


async def test_should_retry_then_dead_letter_an_explanation_whose_order_is_not_known(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    triage = order_triage_payload()

    partition, offset = await produce(
        kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, to_json_bytes(triage)
    )

    await _committed_past(app, kafka_bootstrap, partition, offset)
    dead_letter = await _dead_letter(app, partition, offset)
    assert dead_letter["errorType"] == "OrderContextNotFoundError"
    assert dead_letter["attempts"] == 3
    assert f"no order context for order {triage['orderId']}" in str(dead_letter["errorMessage"])
    assert (
        await app.database["notifications"].count_documents({"sourceEventId": triage["eventId"]})
        == 0
    )


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (b"{not json", "not valid JSON"),
        (to_json_bytes(order_triage_payload(version=2)), "version"),
        (to_json_bytes(order_triage_payload(suggestedAction="REFUND")), "suggestedAction"),
    ],
    ids=["malformed", "unknown-version", "unknown-action"],
)
async def test_should_dead_letter_an_invalid_result_at_once_and_process_the_next_one(
    app: RunningApp, kafka_bootstrap: str, value: bytes, reason: str
) -> None:
    placed = await place_order(app, kafka_bootstrap)
    valid = order_triage_payload(order_id=placed["aggregateId"])
    key = str(placed["aggregateId"]).encode()

    partition, invalid_offset = await produce(
        kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, value, key=key
    )
    await produce(kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, to_json_bytes(valid), key=key)

    await assert_readable_over_http(app, await _explanation_of(app, valid["eventId"]))
    dead_letter = await _dead_letter(app, partition, invalid_offset)
    assert dead_letter["errorType"] == "InvalidMessageError"
    assert dead_letter["attempts"] == 1
    assert reason in str(dead_letter["errorMessage"])


async def test_should_serve_a_cleaned_and_shortened_explanation_as_plain_text(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    placed = await place_order(app, kafka_bootstrap)
    raw = "  <script>alert(1)</script>\x00\x1b[31m Stock\r\n\twas\u202e short. " + "z" * 900
    triage = order_triage_payload(order_id=placed["aggregateId"], explanation=raw)

    await produce(kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, to_json_bytes(triage))

    notification = await _explanation_of(app, triage["eventId"])
    await assert_readable_over_http(app, notification)
    response = await app.http.get(f"/notifications/{notification['_id']}", headers=admin_headers())
    assert response.headers["content-type"] == "application/json"
    message = response.json()["message"]
    assert message.startswith("<script>alert(1)</script>[31m Stock was short. zzz")
    assert len(message) == 500
    assert message.endswith("z…")
    assert not any(ord(char) < 0x20 or char == "\u202e" for char in message)


async def test_should_show_the_explanation_only_to_the_customer_of_the_order(
    app: RunningApp, kafka_bootstrap: str
) -> None:
    placed = await place_order(app, kafka_bootstrap)
    triage = order_triage_payload(order_id=placed["aggregateId"])
    await produce(kafka_bootstrap, ORDER_TRIAGE_COMPLETED_TOPIC, to_json_bytes(triage))
    notification = await _explanation_of(app, triage["eventId"])
    owner = bearer(TOKENS.customer_token(UUID(str(placed["customerId"]))))
    stranger = bearer(TOKENS.customer_token(uuid4()))
    path, query = f"/notifications/{notification['_id']}", {"orderId": str(placed["aggregateId"])}

    own = await app.http.get(path, headers=owner)
    foreign = await app.http.get(path, headers=stranger)
    foreign_list = await app.http.get("/notifications", params=query, headers=stranger)
    foreign_filter = await app.http.get(
        "/notifications", params={"customerId": str(placed["customerId"])}, headers=stranger
    )

    assert own.status_code == 200
    assert own.json()["message"] == triage["explanation"]
    assert foreign.status_code == 404
    unknown = await app.http.get(f"/notifications/{uuid4()}", headers=stranger)
    assert foreign.json() == {"detail": f"notification {notification['_id']} not found"}
    assert unknown.status_code == 404
    assert foreign_list.json()["total"] == 0
    assert foreign_filter.json()["total"] == 0


async def test_should_start_and_serve_explanations_while_kafka_is_unreachable(
    mongodb_url: str,
) -> None:
    settings = component_settings(mongodb_url, "127.0.0.1:1")
    async with run_app(settings) as app:
        ready = await app.http.get("/health/ready")
        listed = await app.http.get(
            "/notifications", params={"type": "ORDER_FAILURE_EXPLANATION"}, headers=admin_headers()
        )

    assert ready.status_code == 200
    assert ready.json()["kafka"] in {"CONNECTING", "DISCONNECTED"}
    assert listed.status_code == 200
    assert listed.json()["total"] == 0
