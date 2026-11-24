"""Tests for the routing table registered by the composition root."""

from datetime import UTC, datetime

from notifications.application.record_order_failure_explanation import (
    RecordOrderFailureExplanationService,
)
from notifications.application.record_order_received import RecordOrderReceivedService
from notifications.application.record_order_status import RecordOrderStatusService
from notifications.application.record_payment_refunded import RecordPaymentRefundedService
from notifications.infrastructure.settings import Settings
from notifications.main import build_routing_table
from tests.support.fakes import (
    FixedClock,
    InMemoryNotificationRepository,
    InMemoryOrderContextRepository,
    RecordingSender,
    SequentialIdGenerator,
)


def _table(settings: Settings) -> tuple[str, ...]:
    notifications, sender = InMemoryNotificationRepository(), RecordingSender()
    order_contexts = InMemoryOrderContextRepository()
    clock, ids = FixedClock(datetime(2026, 11, 16, tzinfo=UTC)), SequentialIdGenerator()
    return build_routing_table(
        settings,
        RecordOrderReceivedService(order_contexts, notifications, sender, clock, ids),
        RecordOrderStatusService(notifications, sender, clock, ids),
        RecordPaymentRefundedService(order_contexts, notifications, sender, clock, ids),
        RecordOrderFailureExplanationService(order_contexts, notifications, sender, clock, ids),
    ).topics


def test_should_route_every_consumed_topic_with_the_default_names() -> None:
    assert _table(Settings(_env_file=None)) == (
        "orders.order-placed",
        "orders.order-confirmed",
        "orders.order-cancelled",
        "payments.payment-refunded",
        "ai.order-triage-completed",
    )


def test_should_route_the_topic_names_given_in_the_settings() -> None:
    settings = Settings(_env_file=None, kafka_topic_order_confirmed="test.order-confirmed")

    assert "test.order-confirmed" in _table(settings)
