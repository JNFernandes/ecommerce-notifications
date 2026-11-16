"""Component tests: the full application against a real MongoDB, with Kafka unreachable."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from notifications.infrastructure.settings import Settings
from notifications.main import create_app

pytestmark = pytest.mark.component

UNREACHABLE_KAFKA = "127.0.0.1:1"


@pytest.fixture
async def client(mongodb_url: str) -> AsyncIterator[AsyncClient]:
    """Run the real lifespan (MongoDB client, indexes, consumer manager) around an HTTP client."""
    app = create_app(
        Settings(
            mongodb_uri=mongodb_url,
            mongodb_database="notifications_component_test",
            kafka_bootstrap_servers=UNREACHABLE_KAFKA,
        )
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield http


async def test_should_start_and_be_ready_against_mongodb_when_kafka_is_unreachable(
    client: AsyncClient,
) -> None:
    live = await client.get("/health/live")
    ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "UP"
    assert body["mongodb"] == "UP"
    assert body["kafka"] in {"CONNECTING", "DISCONNECTED"}


async def test_should_publish_the_health_endpoints_in_the_openapi_document(
    client: AsyncClient,
) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]

    assert set(paths) == {"/health/live", "/health/ready", "/notifications"}
    assert paths["/health/ready"]["get"]["description"]
