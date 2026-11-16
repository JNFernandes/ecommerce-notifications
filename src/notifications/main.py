"""Composition root: wires settings, MongoDB, Kafka and the API into one application.

Run with ``uvicorn notifications.main:create_app --factory``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from notifications.api.app import build_api
from notifications.application.health import HealthService
from notifications.infrastructure.indexes import ensure_indexes
from notifications.infrastructure.mongo import MongoDatabaseProbe, create_mongo_client
from notifications.infrastructure.settings import Settings
from notifications.messaging.consumer_manager import KafkaConsumerManager

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    """Configure the root logger from the settings."""
    logging.basicConfig(
        level=settings.log_level.value,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the application.

    On startup the lifespan creates the MongoDB client, applies the versioned indexes and starts
    the Kafka consumer manager in the background. Kafka does not need to be reachable.
    """
    resolved = settings if settings is not None else Settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = create_mongo_client(resolved)
        consumers = KafkaConsumerManager(resolved.kafka_bootstrap_servers, resolved.kafka_group_id)
        try:
            await ensure_indexes(client[resolved.mongodb_database])
            consumers.start()
            app.state.health_service = HealthService(MongoDatabaseProbe(client), consumers)
            logger.info("ecommerce-notifications started")
            yield
        finally:
            await consumers.stop()
            await client.close()
            logger.info("ecommerce-notifications stopped")

    return build_api(resolved.cors_allowed_origins, lifespan)
