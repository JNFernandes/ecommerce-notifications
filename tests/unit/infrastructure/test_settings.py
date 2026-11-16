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
