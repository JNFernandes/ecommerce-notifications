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
    """Settings for one test: a fresh database and consumer group, fast retries."""
    values: dict[str, object] = {
        "mongodb_uri": mongodb_url,
        "mongodb_database": f"ct_{uuid4().hex}",
        "kafka_bootstrap_servers": kafka_bootstrap,
        "kafka_group_id": f"notifications-test-{uuid4().hex}",
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
