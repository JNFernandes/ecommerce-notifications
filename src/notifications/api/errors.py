"""Error bodies and exception handlers of the HTTP API.

Bodies keep FastAPI's standard ``{"detail": ...}`` shape and are Pydantic models (constitution
Principle V). Errors are mapped to HTTP here, never in the application or domain.
"""

import logging

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import PyMongoError

from notifications.application.errors import (
    AccessTokenUnavailableError,
    InvalidAccessTokenError,
    NotificationNotFoundError,
)
from notifications.domain.access import AccessDeniedError

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    """FastAPI's standard error body."""

    model_config = ConfigDict(frozen=True)

    detail: str = Field(description="Human-readable description of the error.")


async def database_unavailable(request: Request, exc: Exception) -> Response:
    """Answer 503 when MongoDB cannot be reached; never pretend the result is empty."""
    logger.warning("Database error on %s %s: %r", request.method, request.url.path, exc)
    body = ErrorResponse(detail="database unavailable")
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body.model_dump())


async def notification_not_found(request: Request, exc: Exception) -> Response:
    """Answer 404 for an unknown notification id."""
    body = ErrorResponse(detail=str(exc))
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=body.model_dump())


INVALID_TOKEN = ErrorResponse(detail="invalid or missing access token")
"""The one 401 body: identical for every cause, so it reveals nothing about the token."""

ACCESS_DENIED = ErrorResponse(detail="access denied")
"""The 403 body."""

AUTHENTICATION_UNAVAILABLE = ErrorResponse(detail="authentication unavailable")
"""The 503 body when the signing keys cannot be obtained."""


async def invalid_access_token(request: Request, exc: Exception) -> Response:
    """Answer 401 with the generic body and a ``WWW-Authenticate: Bearer`` challenge."""
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=INVALID_TOKEN.model_dump(),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def access_denied(request: Request, exc: Exception) -> Response:
    """Answer 403 to an authenticated caller without a role that grants access."""
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=ACCESS_DENIED.model_dump())


async def authentication_unavailable(request: Request, exc: Exception) -> Response:
    """Answer 503 when tokens cannot be verified because no signing keys are available."""
    logger.warning("Authentication unavailable on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=AUTHENTICATION_UNAVAILABLE.model_dump(),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register the API exception handlers on ``app``."""
    app.add_exception_handler(NotificationNotFoundError, notification_not_found)
    app.add_exception_handler(PyMongoError, database_unavailable)
    app.add_exception_handler(InvalidAccessTokenError, invalid_access_token)
    app.add_exception_handler(AccessDeniedError, access_denied)
    app.add_exception_handler(AccessTokenUnavailableError, authentication_unavailable)
