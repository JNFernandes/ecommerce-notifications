"""Run the real application (lifespan included) against test containers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from notifications.infrastructure.settings import Settings
from notifications.main import create_app

type Document = dict[str, object]


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
    """Settings for one test: a fresh database and consumer group, fast retries (overridable)."""
    values: dict[str, object] = {
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
