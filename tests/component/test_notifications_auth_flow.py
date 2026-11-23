"""Component tests (US-05): the read API with real access tokens, verified against a local JWKS.

Notifications for two customers are consumed from a real Kafka into a real MongoDB; each
customer's token sees only their own, an admin sees them all, and every bad token gets the same
401. Identity's availability never affects startup, readiness or event processing.
"""

import logging
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest

from notifications.infrastructure.settings import Settings
from tests.support.app import (
    TOKENS,
    RunningApp,
    admin_headers,
    assert_readable_over_http,
    assert_stored_readable_over_http,
    bearer,
    component_settings,
    run_app,
    shared_jwks_server,
)
from tests.support.events import (
    ORDER_CONFIRMED_TOPIC,
    order_placed_payload,
    order_status_payload,
    to_json_bytes,
)
from tests.support.kafka import create_consumed_topics, produce
from tests.support.polling import eventually
from tests.support.tokens import JwksServer, TokenIssuer

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


# --- US3: an admin reads every notification -------------------------------------------------


@dataclass(frozen=True)
class Listing:
    """Every notification the API lists for one caller, next to what MongoDB stores."""

    total: object
    stored: int
    ids: list[object]
    customers: set[object]


async def _list_everything(app: RunningApp, headers: dict[str, str]) -> Listing:
    """Page through the unfiltered list; ``stored`` is counted first, to detect a moving set."""
    stored = await app.database["notifications"].count_documents({})
    ids: list[object] = []
    customers: set[object] = set()
    total: object = None
    while total is None or len(ids) < stored:
        _, body = await _list(app, headers, limit="100", offset=str(len(ids)))
        total, page = body["total"], _items(body)
        if not page:
            break
        ids.extend(item["id"] for item in page)
        customers.update(item["customerId"] for item in page)
    return Listing(total, stored, ids, customers)


async def test_should_list_every_customers_notifications_when_an_admin_lists_without_filters(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    # Topics are shared, so this database also receives other tests' events: compare the API
    # with what MongoDB stores instead of assuming four notifications.
    listing = await eventually(
        lambda: _list_everything(app, admin_headers()),
        lambda seen: seen.total == seen.stored == len(seen.ids) == len(set(seen.ids)),
        description="a stable admin listing of every stored notification",
    )

    assert isinstance(listing.total, int)
    assert listing.total >= 4
    assert {str(two_customers.customer_a), str(two_customers.customer_b)} <= listing.customers


async def test_should_filter_by_any_customer_when_an_admin_lists(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    status_a, body_a = await _list(app, admin_headers(), customerId=str(two_customers.customer_a))
    status_b, body_b = await _list(app, admin_headers(), customerId=str(two_customers.customer_b))

    assert (status_a, status_b) == (200, 200)
    assert body_a["total"] == 3
    assert {item["customerId"] for item in _items(body_a)} == {str(two_customers.customer_a)}
    assert body_b["total"] == 1
    assert {item["customerId"] for item in _items(body_b)} == {str(two_customers.customer_b)}


async def test_should_return_any_customers_notification_by_id_when_an_admin_reads_it(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    _, body_a = await _list(app, two_customers.token_a())
    owned_by_a = _items(body_a)[0]

    response = await app.http.get(f"/notifications/{owned_by_a['id']}", headers=admin_headers())

    assert response.status_code == 200
    assert response.json() == owned_by_a


async def test_should_give_the_admin_view_when_the_caller_is_both_customer_and_admin(
    app: RunningApp, two_customers: TwoCustomers
) -> None:
    both = bearer(TOKENS.customer_token(two_customers.customer_a, {"roles": ["CUSTOMER", "ADMIN"]}))

    status, body = await _list(app, both, customerId=str(two_customers.customer_b))
    _, own = await _list(app, two_customers.token_b())
    [notification_b] = _items(own)
    by_id = await app.http.get(f"/notifications/{notification_b['id']}", headers=both)

    assert status == 200
    assert body["total"] == 1
    assert by_id.status_code == 200


async def test_should_answer_403_when_a_valid_token_has_no_admin_role_and_no_customer(
    app: RunningApp,
) -> None:
    tokens = {
        "customer-without-customer-id": TOKENS.sign({"roles": ["CUSTOMER"]}),
        "no-roles": TOKENS.sign({"roles": []}),
        "unknown-role-only": TOKENS.sign({"roles": ["SUPERUSER"], "customerId": uuid4()}),
    }

    answers = {}
    for name, token in tokens.items():
        for path in ("/notifications", f"/notifications/{uuid4()}"):
            response = await app.http.get(path, headers=bearer(token))
            answers[f"{name} {path}"] = (response.status_code, response.content)

    assert set(answers.values()) == {(403, b'{"detail":"access denied"}')}, answers


# --- US4: Identity's availability never affects startup, readiness or event processing ------

UNREACHABLE_JWKS = f"http://127.0.0.1:1{JwksServer.PATH}"
UNREACHABLE_KAFKA = "127.0.0.1:1"


async def test_should_run_and_store_events_but_answer_503_when_identity_is_unreachable(
    mongodb_url: str, kafka_bootstrap: str
) -> None:
    await create_consumed_topics(kafka_bootstrap)
    settings = component_settings(mongodb_url, kafka_bootstrap, identity_jwks_url=UNREACHABLE_JWKS)
    customer, order = uuid4(), uuid4()

    async with run_app(settings) as app:
        live = await app.http.get("/health/live")
        ready = await eventually(
            app.readiness,
            lambda body: body["kafka"] == "CONNECTED",
            description="readiness with Kafka connected",
        )
        placed = order_placed_payload(customerId=str(customer), aggregateId=str(order))
        await produce(kafka_bootstrap, ORDER_PLACED_TOPIC, to_json_bytes(placed))
        stored = await eventually(
            lambda: app.database["notifications"].find_one({"orderId": str(order)}),
            lambda document: document is not None,
            description="the ORDER_RECEIVED notification of this order",
        )
        refused = await app.http.get(
            "/notifications", headers=bearer(TOKENS.customer_token(customer))
        )

        # Identity is back: a second instance on the same database, verifying against the
        # reachable JWKS (and not consuming), reads the notification over HTTP.
        recovered = settings.model_copy(
            update={
                "identity_jwks_url": shared_jwks_server().url,
                "kafka_bootstrap_servers": UNREACHABLE_KAFKA,
            }
        )
        async with run_app(recovered) as restarted:
            assert stored is not None
            await assert_readable_over_http(restarted, stored)

    assert live.status_code == 200
    assert ready["status"] == "UP"
    assert refused.status_code == 503
    assert refused.json() == {"detail": "authentication unavailable"}


@pytest.fixture
def issuer() -> TokenIssuer:
    """An Identity stand-in of its own, so the test controls its JWKS endpoint."""
    return TokenIssuer()


@pytest.fixture
def jwks(issuer: TokenIssuer) -> Iterator[JwksServer]:
    """A JWKS endpoint serving ``issuer``'s key, stopped at the end of the test."""
    with JwksServer([issuer.public_jwk()]) as server:
        yield server


def _isolated_settings(mongodb_url: str, jwks: JwksServer) -> Settings:
    """Settings verifying against ``jwks``; Kafka is unreachable, no event is involved."""
    return component_settings(mongodb_url, UNREACHABLE_KAFKA, identity_jwks_url=jwks.url)


async def test_should_accept_a_cached_key_when_identity_becomes_unreachable(
    mongodb_url: str, issuer: TokenIssuer, jwks: JwksServer
) -> None:
    async with run_app(_isolated_settings(mongodb_url, jwks)) as app:
        before = await app.http.get(
            "/notifications", headers=bearer(issuer.customer_token(uuid4()))
        )
        jwks.stop()
        after = await app.http.get("/notifications", headers=bearer(issuer.customer_token(uuid4())))

    assert (before.status_code, after.status_code) == (200, 200)
    assert jwks.requests == 1


async def test_should_refresh_the_keys_once_when_identity_rotates_to_a_new_kid(
    mongodb_url: str, issuer: TokenIssuer, jwks: JwksServer
) -> None:
    rotated = TokenIssuer()
    settings = _isolated_settings(mongodb_url, jwks)

    async with run_app(settings, jwks_refresh_cooldown_seconds=0.0) as app:
        before = await app.http.get(
            "/notifications", headers=bearer(issuer.customer_token(uuid4()))
        )
        jwks.keys = [rotated.public_jwk()]
        after = await app.http.get(
            "/notifications", headers=bearer(rotated.customer_token(uuid4()))
        )

    assert (before.status_code, after.status_code) == (200, 200)
    assert jwks.requests == 2


async def test_should_not_refetch_for_repeated_unknown_kids_within_the_cooldown(
    mongodb_url: str, issuer: TokenIssuer, jwks: JwksServer
) -> None:
    async with run_app(_isolated_settings(mongodb_url, jwks)) as app:
        known = await app.http.get("/notifications", headers=bearer(issuer.customer_token(uuid4())))
        unknown = [
            await app.http.get(
                "/notifications", headers=bearer(TokenIssuer().customer_token(uuid4()))
            )
            for _ in range(5)
        ]

    assert known.status_code == 200
    assert {response.status_code for response in unknown} == {401}
    assert jwks.requests == 1
