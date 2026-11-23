"""Component tests (US-05): the read API with real access tokens, verified against a local JWKS.

Notifications for two customers are consumed from a real Kafka into a real MongoDB; each
customer's token sees only their own, and every bad token gets the same 401.
"""

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from tests.support.app import (
    TOKENS,
    RunningApp,
    assert_stored_readable_over_http,
    bearer,
    component_settings,
    run_app,
)
from tests.support.events import (
    ORDER_CONFIRMED_TOPIC,
    order_placed_payload,
    order_status_payload,
    to_json_bytes,
)
from tests.support.kafka import create_consumed_topics, produce
from tests.support.polling import eventually
from tests.support.tokens import TokenIssuer

pytestmark = pytest.mark.component

ORDER_PLACED_TOPIC = "orders.order-placed"
INVALID_TOKEN_BODY = b'{"detail":"invalid or missing access token"}'


@pytest.fixture
async def app(mongodb_url: str, kafka_bootstrap: str) -> AsyncIterator[RunningApp]:
    """The application, connected to the test broker, a fresh database and the test JWKS."""
    await create_consumed_topics(kafka_bootstrap)
    async with run_app(component_settings(mongodb_url, kafka_bootstrap)) as running:
        yield running


@dataclass(frozen=True)
class TwoCustomers:
    """Stored notifications: A has two orders (one also confirmed), B has one order."""

    customer_a: UUID
    customer_b: UUID
    first_order_a: UUID
    order_b: UUID

    def token_a(self) -> dict[str, str]:
        """Headers with a CUSTOMER token for A."""
        return bearer(TOKENS.customer_token(self.customer_a))

    def token_b(self) -> dict[str, str]:
        """Headers with a CUSTOMER token for B."""
        return bearer(TOKENS.customer_token(self.customer_b))


async def _stored_count(app: RunningApp, customers: list[UUID]) -> int:
    query = {"customerId": {"$in": [str(customer) for customer in customers]}}
    return await app.database["notifications"].count_documents(query)


@pytest.fixture
async def two_customers(app: RunningApp, kafka_bootstrap: str) -> TwoCustomers:
    """Produce the events of two customers and wait until their four notifications are stored."""
    customer_a, customer_b = uuid4(), uuid4()
    first_order_a, second_order_a, order_b = uuid4(), uuid4(), uuid4()
    for customer, order in [
        (customer_a, first_order_a),
        (customer_a, second_order_a),
        (customer_b, order_b),
    ]:
        placed = order_placed_payload(customerId=str(customer), aggregateId=str(order))
        await produce(kafka_bootstrap, ORDER_PLACED_TOPIC, to_json_bytes(placed))
    confirmed = order_status_payload(customerId=str(customer_a), aggregateId=str(first_order_a))
    await produce(kafka_bootstrap, ORDER_CONFIRMED_TOPIC, to_json_bytes(confirmed))

    await eventually(
        lambda: _stored_count(app, [customer_a, customer_b]),
        lambda count: count == 4,
        description="the four notifications of customers A and B",
    )
    await assert_stored_readable_over_http(
        app, {"customerId": {"$in": [str(customer_a), str(customer_b)]}}
    )
    return TwoCustomers(customer_a, customer_b, first_order_a, order_b)


async def _list(
    app: RunningApp, headers: dict[str, str], **params: str
) -> tuple[int, dict[str, object]]:
    response = await app.http.get("/notifications", params=params, headers=headers)
    body: dict[str, object] = response.json()
    return response.status_code, body


def _items(body: dict[str, object]) -> list[dict[str, object]]:
    items = body["items"]
    assert isinstance(items, list)
    return items


# --- US1: a customer reads only their own notifications -------------------------------------


async def test_should_list_only_the_callers_notifications_when_a_customer_lists(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    status_a, body_a = await _list(app, two_customers.token_a())
    status_b, body_b = await _list(app, two_customers.token_b())

    assert (status_a, status_b) == (200, 200)
    assert body_a["total"] == 3
    assert {item["customerId"] for item in _items(body_a)} == {str(two_customers.customer_a)}
    assert body_b["total"] == 1
    assert {item["customerId"] for item in _items(body_b)} == {str(two_customers.customer_b)}


async def test_should_answer_an_empty_page_when_a_customer_filters_by_another_customer(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    status, body = await _list(
        app, two_customers.token_a(), customerId=str(two_customers.customer_b)
    )

    assert status == 200
    assert body == {"items": [], "total": 0, "limit": 20, "offset": 0}


async def test_should_apply_the_filters_within_the_callers_notifications(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    token = two_customers.token_a()

    _, own = await _list(app, token, customerId=str(two_customers.customer_a))
    _, by_order = await _list(app, token, orderId=str(two_customers.first_order_a))
    _, by_type = await _list(app, token, type="ORDER_RECEIVED")
    _, confirmed = await _list(app, token, type="ORDER_CONFIRMED")
    _, foreign_order = await _list(app, token, orderId=str(two_customers.order_b))

    assert own["total"] == 3
    assert by_order["total"] == 2
    assert {item["orderId"] for item in _items(by_order)} == {str(two_customers.first_order_a)}
    assert by_type["total"] == 2
    assert {item["customerId"] for item in _items(by_type)} == {str(two_customers.customer_a)}
    assert confirmed["total"] == 1
    assert foreign_order["total"] == 0


async def test_should_answer_404_like_an_unknown_id_for_another_customers_notification(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    _, body_b = await _list(app, two_customers.token_b())
    [notification_b] = _items(body_b)
    unknown = uuid4()

    foreign = await app.http.get(
        f"/notifications/{notification_b['id']}", headers=two_customers.token_a()
    )
    missing = await app.http.get(f"/notifications/{unknown}", headers=two_customers.token_a())

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == {"detail": f"notification {notification_b['id']} not found"}
    assert missing.json() == {"detail": f"notification {unknown} not found"}


async def test_should_return_the_callers_own_notification_by_id(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    _, body_a = await _list(app, two_customers.token_a())
    listed = _items(body_a)[0]

    response = await app.http.get(f"/notifications/{listed['id']}", headers=two_customers.token_a())

    assert response.status_code == 200
    assert response.json() == listed


# --- US2: requests without a valid token are refused ----------------------------------------


def _bad_tokens() -> dict[str, str]:
    """Every kind of token the service must refuse, by name."""
    now = int(time.time())
    customer = {"customerId": uuid4()}
    stranger = TokenIssuer(kid=TOKENS.kid)  # same kid, different key: a wrong signature
    return {
        "malformed": "not.a.jwt",
        "garbage": "x" * 40,
        "expired": TOKENS.sign({**customer, "iat": now - 600, "exp": now - 60}),
        "wrong-signature": stranger.sign(customer),
        "unknown-kid": TokenIssuer().sign(customer),
        "wrong-issuer": TOKENS.sign({**customer, "iss": "http://evil.example"}),
        "wrong-audience": TOKENS.sign({**customer, "aud": "another-service"}),
        "no-kid": TOKENS.sign(customer, headers={"kid": None}),
        "alg-none": TOKENS.unsigned(customer),
        "hs256-with-public-key": TOKENS.hs256_with_public_key(customer),
        "rs512-header": TOKENS.sign(customer, headers={"alg": "RS512"}),
        "no-exp": TOKENS.sign(customer, omit=["exp"]),
    }


async def test_should_answer_the_byte_identical_401_to_every_missing_or_invalid_token(
    app: RunningApp, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tokens = _bad_tokens()
    requests: dict[str, dict[str, str]] = {
        "no-header": {},
        "basic-scheme": {"Authorization": "Basic dXNlcjpwYXNz"},
        **{name: bearer(token) for name, token in tokens.items()},
    }

    answers = {}
    for name, headers in requests.items():
        for path in ("/notifications", f"/notifications/{uuid4()}"):
            response = await app.http.get(path, headers=headers)
            answers[f"{name} {path}"] = (
                response.status_code,
                response.content,
                response.headers.get("www-authenticate"),
            )

    assert set(answers.values()) == {(401, INVALID_TOKEN_BODY, "Bearer")}, answers
    for name, token in tokens.items():
        assert token not in caplog.text, f"the {name} token was logged"


async def test_should_accept_a_token_that_expired_within_the_clock_tolerance(
    app: RunningApp,
) -> None:
    now = int(time.time())
    token = TOKENS.customer_token(uuid4(), {"iat": now - 600, "exp": now - 2})

    response = await app.http.get("/notifications", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_should_never_log_an_accepted_token(
    app: RunningApp, two_customers: TwoCustomers, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    headers = two_customers.token_a()

    response = await app.http.get("/notifications", headers=headers)

    token = headers["Authorization"].removeprefix("Bearer ")
    assert response.status_code == 200
    assert token not in caplog.text
    assert token not in response.text
