"""CORS tests for the notification endpoints: origins come only from the configuration."""

from collections.abc import Sequence

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from notifications.api.app import build_api
from notifications.application.auth import Principal
from notifications.application.query_notifications import NotificationQueryService
from notifications.domain.access import Role
from tests.support.fakes import FakeAccessTokenVerifier, FakeNotificationReader

FRONTEND = "http://localhost:5173"
ADMIN = {"admin": Principal(subject="a", roles=frozenset({Role.ADMIN}))}


def _app(allowed: Sequence[str]) -> FastAPI:
    app = build_api(cors_allowed_origins=allowed)
    app.state.notification_query_service = NotificationQueryService(FakeNotificationReader())
    app.state.access_token_verifier = FakeAccessTokenVerifier(ADMIN)
    return app


async def _get(allowed: Sequence[str], origin: str) -> Response:
    async with AsyncClient(transport=ASGITransport(app=_app(allowed)), base_url="http://test") as h:
        return await h.get(
            "/notifications", headers={"Origin": origin, "Authorization": "Bearer admin"}
        )


async def _preflight(method: str, request_headers: str | None = None) -> Response:
    headers = {"Origin": FRONTEND, "Access-Control-Request-Method": method}
    if request_headers is not None:
        headers["Access-Control-Request-Headers"] = request_headers
    async with AsyncClient(
        transport=ASGITransport(app=_app([FRONTEND])), base_url="http://test"
    ) as h:
        return await h.options("/notifications", headers=headers)


async def test_should_allow_a_configured_origin_to_read_notifications() -> None:
    response = await _get([FRONTEND], FRONTEND)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND


async def test_should_not_allow_an_origin_that_is_not_configured() -> None:
    response = await _get([FRONTEND], "http://evil.example")

    assert "access-control-allow-origin" not in response.headers


async def test_should_allow_no_origin_by_default() -> None:
    response = await _get([], FRONTEND)

    assert "access-control-allow-origin" not in response.headers


async def test_should_allow_a_cross_origin_request_with_an_authorization_header() -> None:
    preflight = await _preflight("GET", "authorization")

    assert preflight.status_code == 200
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


async def test_should_not_allow_a_cross_origin_request_with_another_header() -> None:
    preflight = await _preflight("GET", "x-custom-header")

    assert preflight.status_code == 400


async def test_should_not_allow_a_cross_origin_delete() -> None:
    preflight = await _preflight("DELETE")

    assert preflight.status_code == 400
    assert "DELETE" not in preflight.headers.get("access-control-allow-methods", "")
