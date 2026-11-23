"""Tests for the API authentication dependencies and their 401/403/503 answers."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from notifications.api.auth import (
    PUBLIC,
    RoleRequirement,
    ScopeDependency,
    authenticate,
    require_roles,
)
from notifications.api.errors import register_error_handlers
from notifications.application.auth import Principal
from notifications.domain.access import Role
from tests.support.fakes import FakeAccessTokenVerifier

CUSTOMER = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
PRINCIPALS = {
    "customer": Principal(subject="c", customer_id=CUSTOMER, roles=frozenset({Role.CUSTOMER})),
    "admin": Principal(subject="a", roles=frozenset({Role.ADMIN})),
    "customer-without-id": Principal(subject="x", roles=frozenset({Role.CUSTOMER})),
    "no-role": Principal(subject="n", customer_id=CUSTOMER),
}
INVALID = {"detail": "invalid or missing access token"}


def _app(verifier: FakeAccessTokenVerifier | None) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    if verifier is not None:
        app.state.access_token_verifier = verifier
    readers = require_roles(Role.CUSTOMER, Role.ADMIN)

    @app.get("/scope", dependencies=[Depends(readers)])
    async def scope(scope: ScopeDependency) -> dict[str, str | None]:
        return {"customer": None if scope.customer_id is None else str(scope.customer_id)}

    @app.get("/admin")
    async def admin(
        principal: Annotated[Principal, Depends(require_roles(Role.ADMIN))],
    ) -> dict[str, str]:
        return {"subject": principal.subject}

    @app.get("/me")
    async def me(principal: Annotated[Principal, Depends(authenticate)]) -> dict[str, str]:
        return {"subject": principal.subject}

    @app.get("/open", dependencies=[PUBLIC])
    async def open_route() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def verifier() -> FakeAccessTokenVerifier:
    return FakeAccessTokenVerifier(PRINCIPALS)


@pytest.fixture
async def client(verifier: FakeAccessTokenVerifier) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_app(verifier))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_should_scope_a_customer_to_their_own_id(client: AsyncClient) -> None:
    response = await client.get("/scope", headers=_bearer("customer"))

    assert response.status_code == 200
    assert response.json() == {"customer": str(CUSTOMER)}


async def test_should_scope_an_admin_to_every_customer(client: AsyncClient) -> None:
    response = await client.get("/scope", headers=_bearer("admin"))

    assert response.json() == {"customer": None}


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="no-header"),
        pytest.param({"Authorization": "Basic dXNlcjpwYXNz"}, id="basic-scheme"),
        pytest.param({"Authorization": "Bearer"}, id="bearer-without-token"),
        pytest.param({"Authorization": "Bearer "}, id="bearer-with-empty-token"),
        pytest.param({"Authorization": "customer"}, id="no-scheme"),
        pytest.param(_bearer("forged"), id="invalid-token"),
    ],
)
async def test_should_answer_the_identical_401_when_the_token_is_missing_or_invalid(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get("/scope", headers=headers)

    assert response.status_code == 401
    assert response.content == b'{"detail":"invalid or missing access token"}'
    assert response.json() == INVALID
    assert response.headers["www-authenticate"] == "Bearer"


async def test_should_not_call_the_verifier_when_there_is_no_bearer_token(
    client: AsyncClient, verifier: FakeAccessTokenVerifier
) -> None:
    await client.get("/scope", headers={"Authorization": "Basic dXNlcjpwYXNz"})

    assert verifier.verified == []


async def test_should_verify_the_token_once_per_request(
    client: AsyncClient, verifier: FakeAccessTokenVerifier
) -> None:
    await client.get("/scope", headers=_bearer("customer"))

    assert verifier.verified == ["customer"]


@pytest.mark.parametrize("token", ["no-role", "customer-without-id"])
async def test_should_answer_403_when_the_caller_has_no_usable_role(
    client: AsyncClient, token: str
) -> None:
    response = await client.get("/scope", headers=_bearer(token))

    assert response.status_code == 403
    assert response.json() == {"detail": "access denied"}
    assert "www-authenticate" not in response.headers


async def test_should_answer_403_when_the_caller_lacks_the_required_role(
    client: AsyncClient,
) -> None:
    assert (await client.get("/admin", headers=_bearer("customer"))).status_code == 403
    assert (await client.get("/admin", headers=_bearer("admin"))).json() == {"subject": "a"}


async def test_should_authenticate_without_requiring_a_role(client: AsyncClient) -> None:
    response = await client.get("/me", headers=_bearer("no-role"))

    assert response.json() == {"subject": "n"}


async def test_should_answer_503_when_the_signing_keys_are_unavailable() -> None:
    transport = ASGITransport(app=_app(FakeAccessTokenVerifier(unavailable=True)))
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.get("/scope", headers=_bearer("customer"))

    assert response.status_code == 503
    assert response.json() == {"detail": "authentication unavailable"}


async def test_should_serve_a_public_route_without_a_token(client: AsyncClient) -> None:
    response = await client.get("/open")

    assert response.json() == {"status": "ok"}


async def test_should_fail_loudly_when_no_verifier_is_configured() -> None:
    transport = ASGITransport(app=_app(None), raise_app_exceptions=True)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        with pytest.raises(RuntimeError, match="AccessTokenVerifier is not configured"):
            await http.get("/scope", headers=_bearer("customer"))


def test_should_keep_the_roles_in_declaration_order() -> None:
    assert require_roles(Role.CUSTOMER, Role.ADMIN).roles == (Role.CUSTOMER, Role.ADMIN)


def test_should_reject_a_role_requirement_without_roles() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        RoleRequirement(())
