"""Liveness and readiness endpoints: public, they never need an access token."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from notifications.api.auth import PUBLIC
from notifications.application.health import ComponentStatus, ConsumerState, HealthService


class LivenessResponse(BaseModel):
    """Liveness of the process."""

    model_config = ConfigDict(frozen=True)

    status: ComponentStatus = Field(description="Always `UP` when the process answers.")


class ReadinessResponse(BaseModel):
    """Readiness of the service and the state of its dependencies."""

    model_config = ConfigDict(frozen=True)

    status: ComponentStatus = Field(
        description="`UP` when MongoDB is reachable. Kafka never makes the service unready."
    )
    mongodb: ComponentStatus = Field(description="Whether MongoDB answered a ping.")
    kafka: ConsumerState = Field(description="Connection state of the Kafka consumers.")


def get_health_service(request: Request) -> HealthService:
    """Return the health service created by the application lifespan."""
    service = getattr(request.app.state, "health_service", None)
    if not isinstance(service, HealthService):
        raise RuntimeError("HealthService is not configured on the application state")
    return service


HealthServiceDependency = Annotated[HealthService, Depends(get_health_service)]

router = APIRouter(prefix="/health", tags=["health"])

_PUBLIC_EXTRA: dict[str, object] = {"x-public": True}


@router.get(
    "/live",
    summary="Liveness probe",
    description="Answers `UP` whenever the process can serve HTTP requests. Public.",
    dependencies=[PUBLIC],
    openapi_extra=_PUBLIC_EXTRA,
)
def liveness(service: HealthServiceDependency) -> LivenessResponse:
    """Report that the process is alive."""
    return LivenessResponse(status=service.check_liveness())


@router.get(
    "/ready",
    summary="Readiness probe",
    description=(
        "Answers 200 when MongoDB is reachable and 503 otherwise. The Kafka consumer state is "
        "reported but never makes the service unready, so the service stays ready while Kafka "
        "is down. Public: it never needs a token, and Identity never makes the service unready."
    ),
    dependencies=[PUBLIC],
    openapi_extra=_PUBLIC_EXTRA,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readiness(service: HealthServiceDependency, response: Response) -> ReadinessResponse:
    """Report whether the service can handle requests."""
    report = await service.check_readiness()
    if report.status is not ComponentStatus.UP:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status=report.status, mongodb=report.mongodb, kafka=report.kafka)
