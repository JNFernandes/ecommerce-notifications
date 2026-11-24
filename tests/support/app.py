"""Run the real application (lifespan included) against test containers.

Access tokens: ``TOKENS`` stands in for ecommerce-identity. Its public key is served by one local
JWKS endpoint shared by the whole session (``shared_jwks_server``), which ``component_settings``
configures as ``IDENTITY_JWKS_URL``. The readable-over-HTTP helpers send an admin token.
"""

import functools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from notifications.infrastructure.settings import Settings
from notifications.main import create_app
from tests.support.events import order_placed_payload, to_json_bytes
from tests.support.kafka import produce
from tests.support.polling import eventually
from tests.support.tokens import JwksServer, TokenIssuer

type Document = dict[str, object]

TOKENS = TokenIssuer(kid="component-tests")
"""Signs the access tokens of the component tests."""


@functools.cache
def shared_jwks_server() -> JwksServer:
    """The session's JWKS endpoint serving the ``TOKENS`` key, started on first use.

    It runs in a daemon thread until the test process ends.
    """
    return JwksServer([TOKENS.public_jwk()]).start()


def bearer(token: str) -> dict[str, str]:
    """Return the ``Authorization`` header carrying ``token``."""
    return {"Authorization": f"Bearer {token}"}


def admin_headers() -> dict[str, str]:
    """Return headers with a fresh ADMIN token (sees every notification)."""
    return bearer(TOKENS.admin_token())


@dataclass(frozen=True)
class RunningApp:
    """A started application with an HTTP client and direct access to its database."""

    http: AsyncClient
    database: AsyncDatabase[Document]
    settings: Settings

    async def readiness(self) -> dict[str, object]:
        """Return the body of ``GET /health/ready``."""
        body: dict[str, object] = (await self.http.get("/health/ready")).json()
        return body


def component_settings(mongodb_url: str, kafka_bootstrap: str, **overrides: object) -> Settings:
    """Settings for one test: a fresh database and consumer group, fast retries (overridable).

    Tokens are verified with the keys of ``shared_jwks_server`` (``TOKENS``).
    """
    values: dict[str, object] = {
        "identity_jwks_url": shared_jwks_server().url,
        "mongodb_uri": mongodb_url,
        "mongodb_database": f"ct_{uuid4().hex}",
        "kafka_bootstrap_servers": kafka_bootstrap,
        "kafka_group_id": f"notifications-test-{uuid4().hex}",
        # Topics are shared by all component tests and read from the earliest offset by each new
        # consumer group; messages left by other tests (e.g. refunds whose order context lives in
        # another test database) must not cost a full default retry budget each.
        "consumer_retry_base_delay_seconds": 0.05,
        "consumer_retry_max_delay_seconds": 0.1,
    }
    values.update(overrides)
    return Settings.model_validate(values)


@asynccontextmanager
async def run_app(settings: Settings, **app_options: float) -> AsyncIterator[RunningApp]:
    """Start the app with its real lifespan; drop its database afterwards."""
    app = create_app(settings, **app_options)
    client: AsyncMongoClient[Document] = AsyncMongoClient(
        settings.mongodb_uri, document_class=dict, tz_aware=True
    )
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
        ):
            yield RunningApp(http, client[settings.mongodb_database], settings)
    finally:
        await client.drop_database(settings.mongodb_database)
        await client.close()


_API_FIELDS = ("customerId", "orderId", "type", "channel", "message", "status", "sourceEventId")
_TRIAGE_FIELDS = ("suggestedAction", "source", "confidence", "promptVersion")


async def assert_readable_over_http(app: RunningApp, document: Document) -> None:
    """Constitution VII: a stored notification is also readable through the read API, unchanged.

    Reads as an admin, who may see every customer's notifications.
    """
    response = await app.http.get(f"/notifications/{document['_id']}", headers=admin_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == document["_id"]
    assert {field: body[field] for field in _API_FIELDS} == {
        field: document[field] for field in _API_FIELDS
    }
    triage = document.get("triage")
    if triage is None:
        assert {field: body[field] for field in _TRIAGE_FIELDS} == dict.fromkeys(_TRIAGE_FIELDS)
    else:
        assert isinstance(triage, dict)
        expected = {**triage, "confidence": float(str(triage["confidence"]))}
        assert {field: body[field] for field in _TRIAGE_FIELDS} == expected
    stored_at = document["createdAt"]
    assert isinstance(stored_at, datetime)
    assert datetime.fromisoformat(body["createdAt"]) == stored_at


async def assert_stored_readable_over_http(app: RunningApp, query: Document) -> None:
    """Read every notification matching ``query`` back through the API (at least one)."""
    documents = await app.database["notifications"].find(query).to_list()
    assert documents, f"no stored notification matches {query}"
    for document in documents:
        await assert_readable_over_http(app, document)


async def place_order(app: RunningApp, kafka_bootstrap: str) -> dict[str, object]:
    """Produce an ``orders.order-placed`` event and wait until its order context exists.

    Refunds and order failure explanations need that context to find the customer.
    """
    placed = order_placed_payload()
    await produce(kafka_bootstrap, "orders.order-placed", to_json_bytes(placed))
    await eventually(
        lambda: app.database["order_context"].count_documents({"_id": placed["aggregateId"]}),
        lambda count: count == 1,
        description="the order context of the placement",
    )
    return placed
