"""Tests for the notification endpoints, served with an in-memory reader (no MongoDB, no Kafka)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pymongo.errors import ServerSelectionTimeoutError

from notifications.api.app import build_api
from notifications.application.query_notifications import (
    NotificationFilters,
    NotificationQueryService,
)
from notifications.domain.notification import Notification, NotificationType
from tests.support.fakes import FakeNotificationReader

START = datetime(2026, 11, 16, 10, 0, 0, 123_000, tzinfo=UTC)
CUSTOMER = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")


def _notification(minutes: int, customer: UUID = CUSTOMER) -> Notification:
    return Notification.payment_refunded(
        notification_id=uuid4(),
        customer_id=customer,
        order_id=uuid4(),
        amount=Decimal("42.5"),
        currency="USD",
        source_event_id=uuid4(),
        now=START + timedelta(minutes=minutes),
    )


async def _client(reader: FakeNotificationReader) -> AsyncClient:
    app = build_api(cors_allowed_origins=[])
    app.state.notification_query_service = NotificationQueryService(reader)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def reader() -> FakeNotificationReader:
    return FakeNotificationReader([_notification(m) for m in range(3)])


@pytest.fixture
async def client(reader: FakeNotificationReader) -> AsyncIterator[AsyncClient]:
    async with await _client(reader) as http:
        yield http


async def test_should_list_notifications_newest_first_in_the_documented_shape(
    client: AsyncClient, reader: FakeNotificationReader
) -> None:
    response = await client.get("/notifications")

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (3, 20, 0)
    newest = max(reader.notifications, key=lambda n: n.created_at)
    assert body["items"][0] == {
        "id": str(newest.id),
        "customerId": str(newest.customer_id),
        "orderId": str(newest.order_id),
        "type": "PAYMENT_REFUNDED",
        "channel": "email",
        "message": newest.message,
        "status": "SENT",
        "createdAt": "2026-11-16T10:02:00.123000Z",
        "sourceEventId": str(newest.source_event_id),
    }


async def test_should_pass_the_filters_and_paging_to_the_service(
    client: AsyncClient, reader: FakeNotificationReader
) -> None:
    order = reader.notifications[0].order_id

    response = await client.get(
        "/notifications",
        params={
            "customerId": str(CUSTOMER),
            "orderId": str(order),
            "type": "PAYMENT_REFUNDED",
            "limit": 5,
            "offset": 0,
        },
    )

    assert response.status_code == 200
    expected = NotificationFilters(
        customer_id=CUSTOMER, order_id=order, type=NotificationType.PAYMENT_REFUNDED
    )
    assert reader.calls[0] == ("find_page", (expected, 5, 0))
    assert response.json()["total"] == 1


async def test_should_answer_an_empty_page_when_nothing_matches(client: AsyncClient) -> None:
    response = await client.get("/notifications", params={"orderId": str(uuid4())})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_should_answer_503_when_the_database_is_unreachable() -> None:
    reader = FakeNotificationReader(error=ServerSelectionTimeoutError("no server"))
    async with await _client(reader) as http:
        response = await http.get("/notifications")

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


async def test_should_return_one_notification_by_id(
    client: AsyncClient, reader: FakeNotificationReader
) -> None:
    wanted = reader.notifications[1]

    response = await client.get(f"/notifications/{wanted.id}")

    assert response.status_code == 200
    assert response.json()["id"] == str(wanted.id)
    assert response.json()["message"] == wanted.message


async def test_should_answer_404_with_the_standard_body_for_an_unknown_id(
    client: AsyncClient,
) -> None:
    missing = uuid4()

    response = await client.get(f"/notifications/{missing}")

    assert response.status_code == 404
    assert response.json() == {"detail": f"notification {missing} not found"}


def _invalid_fields(response_body: object) -> set[str]:
    assert isinstance(response_body, dict)
    details = response_body["detail"]
    assert isinstance(details, list)
    return {str(error["loc"][-1]) for error in details}


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"limit": "abc"}, "limit"),
        ({"offset": -1}, "offset"),
        ({"type": "FOO"}, "type"),
        ({"customerId": "not-a-uuid"}, "customerId"),
        ({"orderId": "123"}, "orderId"),
    ],
)
async def test_should_reject_invalid_list_parameters_with_422(
    client: AsyncClient, params: dict[str, str | int], field: str
) -> None:
    response = await client.get("/notifications", params=params)

    assert response.status_code == 422
    assert field in _invalid_fields(response.json())


async def test_should_list_the_allowed_types_when_the_type_is_unknown(client: AsyncClient) -> None:
    response = await client.get("/notifications", params={"type": "FOO"})

    message = str(response.json()["detail"][0]["msg"])
    for allowed in ["ORDER_RECEIVED", "ORDER_CONFIRMED", "ORDER_CANCELLED", "PAYMENT_REFUNDED"]:
        assert allowed in message


async def test_should_reject_a_malformed_id_in_the_path_with_422(client: AsyncClient) -> None:
    response = await client.get("/notifications/not-a-uuid")

    assert response.status_code == 422
    assert "notification_id" in _invalid_fields(response.json())


async def test_should_accept_uppercase_uuids_and_query_them_in_lowercase(
    client: AsyncClient, reader: FakeNotificationReader
) -> None:
    wanted = reader.notifications[0]

    by_path = await client.get(f"/notifications/{str(wanted.id).upper()}")
    by_filter = await client.get("/notifications", params={"orderId": str(wanted.order_id).upper()})

    assert by_path.status_code == 200
    assert by_filter.json()["total"] == 1
    assert ("find_by_id", wanted.id) in reader.calls


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/notifications", f"/notifications/{uuid4()}"])
async def test_should_refuse_every_write_method(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path, json={})

    assert response.status_code == 405


async def test_should_ignore_an_unknown_query_parameter(client: AsyncClient) -> None:
    response = await client.get("/notifications", params={"sort": "oldest"})

    assert response.status_code == 200
