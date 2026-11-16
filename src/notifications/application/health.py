"""Health use cases: liveness and readiness of the service and its dependencies."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ComponentStatus(StrEnum):
    """Status of the service or one of its dependencies."""

    UP = "UP"
    DOWN = "DOWN"


class ConsumerState(StrEnum):
    """Connection state of the Kafka consumers."""

    STOPPED = "STOPPED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"


class DatabaseProbe(Protocol):
    """Checks whether the database answers."""

    async def is_reachable(self) -> bool:
        """Return ``True`` when the database answers a ping."""
        ...


class ConsumerStateProvider(Protocol):
    """Reports the current state of the Kafka consumers."""

    @property
    def state(self) -> ConsumerState:
        """The current consumer connection state."""
        ...


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Result of a readiness check.

    The service is ready when MongoDB is reachable. The Kafka consumer state is reported but
    never makes the service unready (Principle VIII, startup resilience).
    """

    mongodb: ComponentStatus
    kafka: ConsumerState

    @property
    def status(self) -> ComponentStatus:
        """Overall readiness, derived from MongoDB only."""
        return self.mongodb


class HealthService:
    """Answers liveness and readiness questions."""

    def __init__(self, database: DatabaseProbe, consumers: ConsumerStateProvider) -> None:
        """Create the service from a database probe and the consumer state provider."""
        self._database = database
        self._consumers = consumers

    def check_liveness(self) -> ComponentStatus:
        """Return ``UP``: answering at all means the process is alive."""
        return ComponentStatus.UP

    async def check_readiness(self) -> ReadinessReport:
        """Probe MongoDB and read the consumer state."""
        reachable = await self._database.is_reachable()
        return ReadinessReport(
            mongodb=ComponentStatus.UP if reachable else ComponentStatus.DOWN,
            kafka=self._consumers.state,
        )
