"""Component tests: the service keeps running through Kafka outages (User Story 5).

The broker is frozen with ``docker pause``, which keeps its address, so the application's
bootstrap setting stays valid. Every test unpauses the broker in ``finally``.
"""

import pytest
from testcontainers.community.kafka import KafkaContainer

from tests.support.app import (
    RunningApp,
    assert_stored_readable_over_http,
    component_settings,
    run_app,
)
from tests.support.events import order_placed_payload, to_json_bytes
from tests.support.kafka import create_consumed_topics, pause_kafka, produce, unpause_kafka
from tests.support.polling import eventually

pytestmark = pytest.mark.component

TOPIC = "orders.order-placed"
OUTAGE_TIMEOUT_SECONDS = 90.0
"""Reconnection backs off up to 30 s and a hanging request times out after 10 s."""


async def _produce_and_wait_for_notification(app: RunningApp, kafka_bootstrap: str) -> None:
    payload = order_placed_payload()
    await produce(kafka_bootstrap, TOPIC, to_json_bytes(payload))
    await eventually(
        lambda: app.database["notifications"].count_documents(
            {"sourceEventId": payload["eventId"]}
        ),
        lambda count: count == 1,
        timeout_seconds=OUTAGE_TIMEOUT_SECONDS,
        description="the notification of an event produced after the outage",
    )
    await assert_stored_readable_over_http(app, {"sourceEventId": payload["eventId"]})


async def test_should_start_without_kafka_and_catch_up_once_it_is_back(
    mongodb_url: str, kafka_bootstrap: str, kafka_container: KafkaContainer
) -> None:
    await create_consumed_topics(kafka_bootstrap)
    settings = component_settings(mongodb_url, kafka_bootstrap)
    pause_kafka(kafka_container)
    try:
        async with run_app(settings, kafka_check_interval_seconds=1.0) as app:
            live = await app.http.get("/health/live")
            ready = await app.http.get("/health/ready")
            assert live.status_code == 200
            assert ready.status_code == 200
            assert ready.json()["kafka"] in {"CONNECTING", "DISCONNECTED"}

            unpause_kafka(kafka_container)
            await eventually(
                app.readiness,
                lambda body: body["kafka"] == "CONNECTED",
                timeout_seconds=OUTAGE_TIMEOUT_SECONDS,
                description="the consumer to connect after the broker came back",
            )
            await _produce_and_wait_for_notification(app, kafka_bootstrap)
    finally:
        unpause_kafka_if_paused(kafka_container)


async def test_should_stay_ready_during_an_outage_and_reconnect_by_itself(
    mongodb_url: str, kafka_bootstrap: str, kafka_container: KafkaContainer
) -> None:
    await create_consumed_topics(kafka_bootstrap)
    settings = component_settings(mongodb_url, kafka_bootstrap)
    async with run_app(settings, kafka_check_interval_seconds=1.0) as app:
        await eventually(
            app.readiness,
            lambda body: body["kafka"] == "CONNECTED",
            description="the consumer to connect",
        )
        pause_kafka(kafka_container)
        try:
            await eventually(
                app.readiness,
                lambda body: body["kafka"] in {"CONNECTING", "DISCONNECTED"},
                timeout_seconds=OUTAGE_TIMEOUT_SECONDS,
                description="the outage to be detected",
            )
            ready = await app.http.get("/health/ready")
            assert ready.status_code == 200
            assert ready.json()["status"] == "UP"
        finally:
            unpause_kafka_if_paused(kafka_container)
        await _produce_and_wait_for_notification(app, kafka_bootstrap)
        assert (await app.readiness())["kafka"] == "CONNECTED"


def unpause_kafka_if_paused(container: KafkaContainer) -> None:
    """Unpause the broker unless it is already running."""
    wrapped = container.get_wrapped_container()
    wrapped.reload()
    if wrapped.status == "paused":
        unpause_kafka(container)
