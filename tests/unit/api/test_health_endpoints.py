"""Tests for the health endpoints, served without MongoDB or Kafka."""

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from notifications.api.app import build_api
from notifications.application.health import ConsumerState, HealthService
from tests.support.fakes import FakeConsumerStateProvider, FakeDatabaseProbe


@pytest.fixture
def database() -> FakeDatabaseProbe:
    return FakeDatabaseProbe(reachable=True)


@pytest.fixture
def consumers() -> FakeConsumerStateProvider:
    return FakeConsumerStateProvider(ConsumerState.DISCONNECTED)


@pytest.fixture
async def client(
    database: FakeDatabaseProbe, consumers: FakeConsumerStateProvider
) -> AsyncIterator[AsyncClient]:
    app = build_api(cors_allowed_origins=["http://localhost:5173"])
    app.state.health_service = HealthService(database, consumers)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_should_answer_up_on_the_liveness_endpoint(client: AsyncClient) -> None:
    response = await client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


async def test_should_be_ready_and_report_kafka_down_when_only_mongodb_is_reachable(
    client: AsyncClient,
) -> None:
    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "UP", "mongodb": "UP", "kafka": "DISCONNECTED"}


async def test_should_answer_503_on_readiness_when_mongodb_is_unreachable(
    client: AsyncClient, database: FakeDatabaseProbe, consumers: FakeConsumerStateProvider
) -> None:
    database.reachable = False
    consumers.state = ConsumerState.CONNECTED

    response = await client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "DOWN", "mongodb": "DOWN", "kafka": "CONNECTED"}


async def test_should_allow_get_requests_from_a_configured_origin(client: AsyncClient) -> None:
    response = await client.get("/health/live", headers={"Origin": "http://localhost:5173"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_should_not_send_cors_headers_when_no_origin_is_configured() -> None:
    app = build_api(cors_allowed_origins=[])
    app.state.health_service = HealthService(
        FakeDatabaseProbe(reachable=True), FakeConsumerStateProvider(ConsumerState.CONNECTED)
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/health/live", headers={"Origin": "http://localhost:5173"})

    assert "access-control-allow-origin" not in response.headers


async def test_should_fail_loudly_when_the_health_service_is_not_configured() -> None:
    app = build_api(cors_allowed_origins=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        with pytest.raises(RuntimeError, match="HealthService is not configured"):
            await http.get("/health/live")
