"""CORS tests for the notification endpoints: origins come only from the configuration."""

from collections.abc import Sequence

from httpx import ASGITransport, AsyncClient, Response

from notifications.api.app import build_api
from notifications.application.query_notifications import NotificationQueryService
from tests.support.fakes import FakeNotificationReader

FRONTEND = "http://localhost:5173"


async def _get(allowed: Sequence[str], origin: str) -> Response:
    app = build_api(cors_allowed_origins=allowed)
    app.state.notification_query_service = NotificationQueryService(FakeNotificationReader())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        return await http.get("/notifications", headers={"Origin": origin})


async def test_should_allow_a_configured_origin_to_read_notifications() -> None:
    response = await _get([FRONTEND], FRONTEND)

    assert response.headers["access-control-allow-origin"] == FRONTEND


async def test_should_not_allow_an_origin_that_is_not_configured() -> None:
    response = await _get([FRONTEND], "http://evil.example")

    assert "access-control-allow-origin" not in response.headers


async def test_should_allow_no_origin_by_default() -> None:
    response = await _get([], FRONTEND)

    assert "access-control-allow-origin" not in response.headers


async def test_should_not_allow_a_cross_origin_delete() -> None:
    app = build_api(cors_allowed_origins=[FRONTEND])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        preflight = await http.options(
            "/notifications",
            headers={"Origin": FRONTEND, "Access-Control-Request-Method": "DELETE"},
        )

    assert preflight.status_code == 400
    assert "DELETE" not in preflight.headers.get("access-control-allow-methods", "")
