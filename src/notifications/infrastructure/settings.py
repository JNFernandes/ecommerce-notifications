"""Service configuration read from environment variables, with defaults for local development."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LogLevel(StrEnum):
    """Log levels accepted by ``LOG_LEVEL``."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Settings(BaseSettings):
    """Settings for the notifications service.

    Every field maps to an upper-case environment variable of the same name (for example
    ``MONGODB_URI``). The defaults point at the shared ``ecommerce-infra`` stack on localhost.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore", case_sensitive=False)

    mongodb_uri: str = Field(
        default="mongodb://mongo_user:mongo_pass@localhost:27017/?authSource=admin",
        description="MongoDB connection string.",
    )
    mongodb_database: str = Field(
        default="notifications",
        description="Database that holds the notification collections.",
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Comma-separated Kafka bootstrap servers.",
    )
    kafka_group_id: str = Field(
        default="notifications-service",
        description="Kafka consumer group id.",
    )
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Comma-separated origins allowed by CORS. Empty means no cross-origin access.",
    )
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Root log level.")

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        """Accept the log level in any case."""
        return value.upper() if isinstance(value, str) else value
