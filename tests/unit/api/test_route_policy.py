"""Tests for the startup route policy check (default deny)."""

import pytest
from fastapi import APIRouter, Depends, FastAPI

from notifications.api.auth import PUBLIC, require_roles
from notifications.api.route_policy import RoutePolicyError, assert_route_policies
from notifications.domain.access import Role


def test_should_accept_public_routes_role_routes_and_the_docs_routes() -> None:
    app = FastAPI()
    health = APIRouter(prefix="/health", dependencies=[PUBLIC])
    readers = APIRouter(dependencies=[Depends(require_roles(Role.CUSTOMER, Role.ADMIN))])

    @health.get("/live")
    async def live() -> None: ...

    @readers.get("/things")
    async def things() -> None: ...

    @app.get("/admin", dependencies=[Depends(require_roles(Role.ADMIN))])
    async def admin() -> None: ...

    app.include_router(health)
    app.include_router(readers)

    assert {"/docs", "/redoc", "/openapi.json"} <= {getattr(r, "path", "") for r in app.routes}
    assert_route_policies(app)


def test_should_find_a_role_requirement_nested_in_another_dependency() -> None:
    app = FastAPI()
    requirement = require_roles(Role.ADMIN)

    async def nested(principal: object = Depends(requirement)) -> None: ...

    @app.get("/nested", dependencies=[Depends(nested)])
    async def handler() -> None: ...

    assert_route_policies(app)


def test_should_reject_a_route_that_declares_no_access() -> None:
    app = FastAPI()

    @app.get("/secrets")
    async def secrets() -> None: ...

    with pytest.raises(RoutePolicyError, match=r"GET /secrets declares neither"):
        assert_route_policies(app)


def test_should_reject_a_route_that_is_both_public_and_role_restricted() -> None:
    app = FastAPI()

    @app.get("/confused", dependencies=[PUBLIC, Depends(require_roles(Role.ADMIN))])
    async def confused() -> None: ...

    with pytest.raises(RoutePolicyError, match=r"GET /confused declares both"):
        assert_route_policies(app)
