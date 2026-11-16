"""Composition root: wires settings, MongoDB, Kafka and the API into one application.

Run with ``uvicorn notifications.main:create_app --factory``.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from notifications.api.app import build_api
from notifications.application.health import HealthService
from notifications.application.query_notifications import NotificationQueryService
from notifications.application.record_order_received import RecordOrderReceivedService
from notifications.application.record_order_status import RecordOrderStatusService
from notifications.application.record_payment_refunded import RecordPaymentRefundedService
from notifications.infrastructure.indexes import ensure_indexes
from notifications.infrastructure.mongo import MongoDatabaseProbe, create_mongo_client
from notifications.infrastructure.repositories.dead_letters import MongoDeadLetterStore
from notifications.infrastructure.repositories.notifications import MongoNotificationRepository
from notifications.infrastructure.repositories.order_contexts import MongoOrderContextRepository
from notifications.infrastructure.sender import LoggingNotificationSender
from notifications.infrastructure.settings import Settings
from notifications.infrastructure.system import SystemClock, Uuid4Generator
from notifications.messaging.consumer_manager import KafkaConsumerManager
from notifications.messaging.order_placed import parse_order_placed
from notifications.messaging.order_status import parse_order_cancelled, parse_order_confirmed
from notifications.messaging.payment_refunded import parse_payment_refunded
from notifications.messaging.processor import RetryingMessageProcessor
from notifications.messaging.retry import RetryPolicy, StopAwareSleeper
from notifications.messaging.routing import Route, RoutingTable

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings) -> None:
    """Configure the root logger from the settings."""
    logging.basicConfig(
        level=settings.log_level.value,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def build_routing_table(
    settings: Settings,
    record_order_received: RecordOrderReceivedService,
    record_order_status: RecordOrderStatusService,
    record_payment_refunded: RecordPaymentRefundedService,
) -> RoutingTable:
    """Register one route per consumed topic: topic -> parser -> one service method."""
    return RoutingTable(
        [
            Route(
                settings.kafka_topic_order_placed,
                parse_order_placed,
                record_order_received.handle,
            ),
            Route(
                settings.kafka_topic_order_confirmed,
                parse_order_confirmed,
                record_order_status.record_confirmed,
            ),
            Route(
                settings.kafka_topic_order_cancelled,
                parse_order_cancelled,
                record_order_status.record_cancelled,
            ),
            Route(
                settings.kafka_topic_payment_refunded,
                parse_payment_refunded,
                record_payment_refunded.handle,
            ),
        ]
    )


def create_app(
    settings: Settings | None = None, *, kafka_check_interval_seconds: float = 10.0
) -> FastAPI:
    """Create the application.

    On startup the lifespan creates the MongoDB client, applies the versioned indexes and starts
    the Kafka consumer manager in the background. Kafka does not need to be reachable.

    Args:
        settings: Configuration; read from the environment when omitted.
        kafka_check_interval_seconds: How often the broker connection is verified (tests
            shorten it to observe outages quickly).
    """
    resolved = settings if settings is not None else Settings()
    configure_logging(resolved)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = create_mongo_client(resolved)
        database = client[resolved.mongodb_database]
        clock, ids = SystemClock(), Uuid4Generator()
        notifications, sender = MongoNotificationRepository(database), LoggingNotificationSender()
        order_contexts = MongoOrderContextRepository(database)
        record_order_received = RecordOrderReceivedService(
            order_contexts, notifications, sender, clock, ids
        )
        record_payment_refunded = RecordPaymentRefundedService(
            order_contexts, notifications, sender, clock, ids
        )
        record_order_status = RecordOrderStatusService(notifications, sender, clock, ids)
        stop_event = asyncio.Event()
        consumers = KafkaConsumerManager(
            resolved.kafka_bootstrap_servers,
            resolved.kafka_group_id,
            routing=build_routing_table(
                resolved, record_order_received, record_order_status, record_payment_refunded
            ),
            processor=RetryingMessageProcessor(
                RetryPolicy(
                    max_attempts=resolved.consumer_retry_max_attempts,
                    base_delay=resolved.consumer_retry_base_delay_seconds,
                    multiplier=resolved.consumer_retry_multiplier,
                    max_delay=resolved.consumer_retry_max_delay_seconds,
                ),
                MongoDeadLetterStore(database),
                StopAwareSleeper(stop_event),
                clock,
            ),
            stop_event=stop_event,
            check_interval_seconds=kafka_check_interval_seconds,
        )
        try:
            await ensure_indexes(database)
            consumers.start()
            app.state.health_service = HealthService(MongoDatabaseProbe(client), consumers)
            app.state.notification_query_service = NotificationQueryService(notifications)
            logger.info("ecommerce-notifications started")
            yield
        finally:
            await consumers.stop()
            await client.close()
            logger.info("ecommerce-notifications stopped")

    return build_api(resolved.cors_allowed_origins, lifespan)
