"""Tests for environment-based settings."""

import pytest

from notifications.infrastructure.settings import LogLevel, Settings


def test_should_use_local_infrastructure_defaults_when_no_environment_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "MONGODB_URI",
        "MONGODB_DATABASE",
        "KAFKA_BOOTSTRAP_SERVERS",
        "KAFKA_GROUP_ID",
        "CORS_ALLOWED_ORIGINS",
        "LOG_LEVEL",
        "CONSUMER_RETRY_MAX_ATTEMPTS",
        "CONSUMER_RETRY_BASE_DELAY_SECONDS",
        "CONSUMER_RETRY_MULTIPLIER",
        "CONSUMER_RETRY_MAX_DELAY_SECONDS",
        "KAFKA_TOPIC_ORDER_PLACED",
        "KAFKA_TOPIC_ORDER_CONFIRMED",
        "KAFKA_TOPIC_ORDER_CANCELLED",
        "KAFKA_TOPIC_PAYMENT_REFUNDED",
        "IDENTITY_JWKS_URL",
        "JWT_ISSUER",
        "JWT_AUDIENCE",
        "JWT_CLOCK_TOLERANCE_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert (
        settings.mongodb_uri == "mongodb://mongo_user:mongo_pass@localhost:27017/?authSource=admin"
    )
    assert settings.mongodb_database == "notifications"
    assert settings.kafka_bootstrap_servers == "localhost:9092"
    assert settings.kafka_group_id == "notifications-service"
    assert settings.cors_allowed_origins == []
    assert settings.log_level is LogLevel.INFO
    assert settings.consumer_retry_max_attempts == 5
    assert settings.consumer_retry_base_delay_seconds == 1.0
    assert settings.consumer_retry_multiplier == 2.0
    assert settings.consumer_retry_max_delay_seconds == 10.0
    assert settings.kafka_topic_order_placed == "orders.order-placed"
    assert settings.kafka_topic_order_confirmed == "orders.order-confirmed"
    assert settings.kafka_topic_order_cancelled == "orders.order-cancelled"
    assert settings.kafka_topic_payment_refunded == "payments.payment-refunded"
    assert settings.kafka_topic_order_triage_completed == "ai.order-triage-completed"
    assert settings.identity_jwks_url == "http://localhost:3001/.well-known/jwks.json"
    assert settings.jwt_issuer == "http://localhost:3001"
    assert settings.jwt_audience == "ecommerce"
    assert settings.jwt_clock_tolerance_seconds == 5


def test_should_read_comma_separated_cors_origins_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "http://localhost:5173, https://shop.example ,")

    settings = Settings(_env_file=None)

    assert settings.cors_allowed_origins == ["http://localhost:5173", "https://shop.example"]


def test_should_accept_a_lower_case_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")

    assert Settings(_env_file=None).log_level is LogLevel.DEBUG


def test_should_reject_an_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ValueError, match="log_level"):
        Settings(_env_file=None)


def test_should_read_the_retry_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONSUMER_RETRY_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("CONSUMER_RETRY_BASE_DELAY_SECONDS", "0.5")
    monkeypatch.setenv("CONSUMER_RETRY_MULTIPLIER", "3")
    monkeypatch.setenv("CONSUMER_RETRY_MAX_DELAY_SECONDS", "4.5")

    settings = Settings(_env_file=None)

    assert settings.consumer_retry_max_attempts == 3
    assert settings.consumer_retry_base_delay_seconds == 0.5
    assert settings.consumer_retry_multiplier == 3.0
    assert settings.consumer_retry_max_delay_seconds == 4.5


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("CONSUMER_RETRY_MAX_ATTEMPTS", "0", "consumer_retry_max_attempts"),
        ("CONSUMER_RETRY_BASE_DELAY_SECONDS", "0", "consumer_retry_base_delay_seconds"),
        ("CONSUMER_RETRY_MULTIPLIER", "0.5", "consumer_retry_multiplier"),
        ("CONSUMER_RETRY_MAX_DELAY_SECONDS", "0.5", "consumer_retry_max_delay_seconds"),
    ],
)
def test_should_reject_an_invalid_retry_setting(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=field):
        Settings(_env_file=None)


def test_should_read_a_topic_name_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_ORDER_CONFIRMED", "test.order-confirmed")

    assert Settings(_env_file=None).kafka_topic_order_confirmed == "test.order-confirmed"


def test_should_reject_an_empty_topic_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_ORDER_CANCELLED", "")

    with pytest.raises(ValueError, match="kafka_topic_order_cancelled"):
        Settings(_env_file=None)


def test_should_reject_two_consumed_topics_with_the_same_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_ORDER_CONFIRMED", "orders.order-placed")

    with pytest.raises(ValueError, match="consumed topics must be distinct") as raised:
        Settings(_env_file=None)

    assert "kafka_topic_order_confirmed" in str(raised.value)


def test_should_read_the_refund_topic_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_PAYMENT_REFUNDED", "test.payment-refunded")

    assert Settings(_env_file=None).kafka_topic_payment_refunded == "test.payment-refunded"


def test_should_reject_a_refund_topic_equal_to_an_order_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_PAYMENT_REFUNDED", "orders.order-cancelled")

    with pytest.raises(ValueError, match="consumed topics must be distinct") as raised:
        Settings(_env_file=None)

    assert "kafka_topic_payment_refunded" in str(raised.value)


def test_should_read_the_triage_topic_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_ORDER_TRIAGE_COMPLETED", "test.order-triage-completed")

    assert (
        Settings(_env_file=None).kafka_topic_order_triage_completed == "test.order-triage-completed"
    )


def test_should_reject_a_triage_topic_equal_to_another_consumed_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_TOPIC_ORDER_TRIAGE_COMPLETED", "orders.order-placed")

    with pytest.raises(ValueError, match="consumed topics must be distinct") as raised:
        Settings(_env_file=None)

    assert "kafka_topic_order_triage_completed" in str(raised.value)


def test_should_read_the_token_settings_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IDENTITY_JWKS_URL", "https://identity.example/.well-known/jwks.json")
    monkeypatch.setenv("JWT_ISSUER", "https://identity.example")
    monkeypatch.setenv("JWT_AUDIENCE", "shop")
    monkeypatch.setenv("JWT_CLOCK_TOLERANCE_SECONDS", "300")

    settings = Settings(_env_file=None)

    assert settings.identity_jwks_url == "https://identity.example/.well-known/jwks.json"
    assert settings.jwt_issuer == "https://identity.example"
    assert settings.jwt_audience == "shop"
    assert settings.jwt_clock_tolerance_seconds == 300


def test_should_accept_a_zero_clock_tolerance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_CLOCK_TOLERANCE_SECONDS", "0")

    assert Settings(_env_file=None).jwt_clock_tolerance_seconds == 0


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        ("IDENTITY_JWKS_URL", "ftp://identity.example/jwks.json", "identity_jwks_url"),
        ("IDENTITY_JWKS_URL", "localhost:3001/jwks.json", "identity_jwks_url"),
        ("IDENTITY_JWKS_URL", "http://", "identity_jwks_url"),
        ("JWT_ISSUER", "", "jwt_issuer"),
        ("JWT_AUDIENCE", "", "jwt_audience"),
        ("JWT_CLOCK_TOLERANCE_SECONDS", "-1", "jwt_clock_tolerance_seconds"),
        ("JWT_CLOCK_TOLERANCE_SECONDS", "301", "jwt_clock_tolerance_seconds"),
    ],
)
def test_should_reject_an_invalid_token_setting(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    monkeypatch.setenv(variable, value)

    with pytest.raises(ValueError, match=field):
        Settings(_env_file=None)
