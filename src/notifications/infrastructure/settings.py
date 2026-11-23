"""Service configuration read from environment variables, with defaults for local development."""

from enum import StrEnum
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator
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
    kafka_topic_order_placed: str = Field(
        default="orders.order-placed",
        min_length=1,
        description="Topic of OrderPlaced events (ORDER_RECEIVED notifications).",
    )
    kafka_topic_order_confirmed: str = Field(
        default="orders.order-confirmed",
        min_length=1,
        description="Topic of OrderConfirmed events (ORDER_CONFIRMED notifications).",
    )
    kafka_topic_order_cancelled: str = Field(
        default="orders.order-cancelled",
        min_length=1,
        description="Topic of OrderCancelled events (ORDER_CANCELLED notifications).",
    )
    kafka_topic_payment_refunded: str = Field(
        default="payments.payment-refunded",
        min_length=1,
        description="Topic of PaymentRefunded events (PAYMENT_REFUNDED notifications).",
    )
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Comma-separated origins allowed by CORS. Empty means no cross-origin access.",
    )
    log_level: LogLevel = Field(default=LogLevel.INFO, description="Root log level.")
    identity_jwks_url: str = Field(
        default="http://localhost:3001/.well-known/jwks.json",
        description=(
            "http(s) URL of the ecommerce-identity JSON Web Key Set used to verify access tokens. "
            "Fetched lazily on the first request that needs it, never at startup."
        ),
    )
    jwt_issuer: str = Field(
        default="http://localhost:3001",
        min_length=1,
        description="Required ``iss`` claim of every access token.",
    )
    jwt_audience: str = Field(
        default="ecommerce",
        min_length=1,
        description="Required ``aud`` claim of every access token.",
    )
    jwt_clock_tolerance_seconds: int = Field(
        default=5,
        ge=0,
        le=300,
        description="Allowed clock skew, in seconds, when checking ``exp`` and ``iat``.",
    )
    consumer_retry_max_attempts: int = Field(
        default=5,
        ge=1,
        description="Attempts per consumed message, the first included. 1 means no retries.",
    )
    consumer_retry_base_delay_seconds: float = Field(
        default=1.0, gt=0, description="Pause before the second attempt, in seconds."
    )
    consumer_retry_multiplier: float = Field(
        default=2.0, ge=1, description="Growth factor of the pause between attempts."
    )
    consumer_retry_max_delay_seconds: float = Field(
        default=10.0,
        gt=0,
        description=(
            "Cap on any single pause, in seconds. Also the pause between rounds while the "
            "dead-letter store is unavailable."
        ),
    )

    @model_validator(mode="after")
    def _distinct_topics(self) -> Self:
        """Reject two consumed topics with the same name (one route per topic)."""
        topics = {
            "kafka_topic_order_placed": self.kafka_topic_order_placed,
            "kafka_topic_order_confirmed": self.kafka_topic_order_confirmed,
            "kafka_topic_order_cancelled": self.kafka_topic_order_cancelled,
            "kafka_topic_payment_refunded": self.kafka_topic_payment_refunded,
        }
        if len(set(topics.values())) != len(topics):
            raise ValueError(f"the consumed topics must be distinct: {topics}")
        return self

    @model_validator(mode="after")
    def _max_delay_not_below_base_delay(self) -> Self:
        """Reject a maximum delay smaller than the base delay."""
        if self.consumer_retry_max_delay_seconds < self.consumer_retry_base_delay_seconds:
            raise ValueError(
                "consumer_retry_max_delay_seconds must be >= consumer_retry_base_delay_seconds"
            )
        return self

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("identity_jwks_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        """Accept only an absolute http(s) URL with a host."""
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("must be an absolute http(s) URL")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: object) -> object:
        """Accept the log level in any case."""
        return value.upper() if isinstance(value, str) else value
