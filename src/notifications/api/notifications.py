"""Read-only notification endpoints (US-04). Routers do I/O only: one service call per request."""

from datetime import datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from notifications.api.errors import ErrorResponse
from notifications.application.query_notifications import (
    NotificationFilters,
    NotificationQueryService,
)
from notifications.domain.notification import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)


class NotificationResponse(BaseModel):
    """A recorded notification, as returned to clients."""

    model_config = ConfigDict(frozen=True, serialize_by_alias=True, validate_by_name=True)

    id: UUID = Field(description="Notification id.")
    customer_id: UUID = Field(alias="customerId", description="Customer the notification is for.")
    order_id: UUID = Field(alias="orderId", description="Order the notification is about.")
    type: NotificationType = Field(description="What happened to the order or its payment.")
    channel: NotificationChannel = Field(description="Delivery channel (sending is simulated).")
    message: str = Field(description="Text shown to the customer.")
    status: NotificationStatus = Field(description="Delivery status.")
    created_at: datetime = Field(
        alias="createdAt", description="When the notification was recorded (ISO 8601, UTC)."
    )
    source_event_id: UUID = Field(
        alias="sourceEventId", description="Id of the event that produced the notification."
    )

    @classmethod
    def from_domain(cls, notification: Notification) -> Self:
        """Map a domain notification to its response representation."""
        return cls(
            id=notification.id,
            customer_id=notification.customer_id,
            order_id=notification.order_id,
            type=notification.type,
            channel=notification.channel,
            message=notification.message,
            status=notification.status,
            created_at=notification.created_at,
            source_event_id=notification.source_event_id,
        )


class NotificationPageResponse(BaseModel):
    """One page of notifications, newest first."""

    model_config = ConfigDict(frozen=True)

    items: list[NotificationResponse] = Field(description="The notifications of this page.")
    total: int = Field(description="Number of notifications matching the filters (all pages).")
    limit: int = Field(description="Maximum number of items in this page, as applied.")
    offset: int = Field(description="Number of matching notifications skipped, as applied.")


def get_notification_query_service(request: Request) -> NotificationQueryService:
    """Return the query service created by the application lifespan."""
    service = getattr(request.app.state, "notification_query_service", None)
    if not isinstance(service, NotificationQueryService):
        raise RuntimeError("NotificationQueryService is not configured on the application state")
    return service


QueryServiceDependency = Annotated[
    NotificationQueryService, Depends(get_notification_query_service)
]

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    summary="List notifications",
    description=(
        "Notifications newest first (creation time descending, id descending as tie-break), "
        "filtered by any combination of customer, order and type, one page at a time."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The database is unreachable.",
        }
    },
)
async def list_notifications(
    service: QueryServiceDependency,
    customer_id: Annotated[
        UUID | None, Query(alias="customerId", description="Only this customer's notifications.")
    ] = None,
    order_id: Annotated[
        UUID | None, Query(alias="orderId", description="Only notifications about this order.")
    ] = None,
    notification_type: Annotated[
        NotificationType | None, Query(alias="type", description="Only this notification type.")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size, 1 to 100.")] = 20,
    offset: Annotated[
        int, Query(ge=0, description="Number of matching notifications to skip.")
    ] = 0,
) -> NotificationPageResponse:
    """Return one page of matching notifications and the total count."""
    page = await service.list(
        NotificationFilters(customer_id=customer_id, order_id=order_id, type=notification_type),
        limit,
        offset,
    )
    return NotificationPageResponse(
        items=[NotificationResponse.from_domain(n) for n in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{notification_id}",
    summary="Get one notification",
    description="The notification with this id.",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "No notification has this id.",
        },
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "The database is unreachable.",
        },
    },
)
async def get_notification(
    service: QueryServiceDependency,
    notification_id: Annotated[UUID, Path(description="Id of the notification.")],
) -> NotificationResponse:
    """Return one notification, or 404 when it does not exist."""
    return NotificationResponse.from_domain(await service.get(notification_id))
