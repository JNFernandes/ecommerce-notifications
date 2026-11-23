"""FastAPI application assembly: routers and middleware, without any I/O wiring."""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from notifications.api import health, notifications
from notifications.api.errors import register_error_handlers
from notifications.api.route_policy import assert_route_policies

type Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


def build_api(cors_allowed_origins: Sequence[str], lifespan: Lifespan | None = None) -> FastAPI:
    """Create the FastAPI application with its routers.

    The HTTP surface is read-only (Principle II). CORS is only enabled for the configured origins
    and allows ``GET`` requests with an ``Authorization`` header only. Every route must declare its
    access (public or roles), otherwise this raises ``RoutePolicyError`` (default deny). The
    lifespan puts the services and the ``AccessTokenVerifier`` on ``app.state``.
    """
    app = FastAPI(
        title="ecommerce-notifications",
        summary="Read-only access to the customer notifications recorded from order events.",
        version="0.1.0",
        lifespan=lifespan,
    )
    if cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_allowed_origins),
            allow_methods=["GET"],
            allow_headers=["Authorization"],
        )
    app.include_router(health.router)
    app.include_router(notifications.router)
    assert_route_policies(app)
    register_error_handlers(app)
    return app
