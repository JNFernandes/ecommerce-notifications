"""Topic routing table: each consumed topic maps to its parser and one application service method.

Adding a topic is one ``Route`` registration (see ``notifications.main``); the consumer, the
processor and the commit logic do not change. A route contains no logic: it parses the message
(I/O and validation) and calls exactly one service method (constitution Principle I).
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from notifications.messaging.messages import ConsumedMessage, InvalidMessageError


class RunnableRoute(Protocol):
    """A route as the table sees it: a topic and a way to process one of its messages."""

    @property
    def topic(self) -> str:
        """The topic this route consumes."""
        ...

    async def run(self, message: ConsumedMessage) -> None:
        """Parse ``message`` and call the route's service method once."""
        ...


@dataclass(frozen=True, slots=True)
class Route[C]:
    """Connects a topic to its parser and to exactly one application service method.

    ``C`` is the command type: the parser's output and the service method's input, so the pair is
    checked by mypy.
    """

    topic: str
    """The consumed topic."""
    parse: Callable[[ConsumedMessage], C]
    """Decodes and validates a message; raises ``InvalidMessageError`` for invalid content."""
    handle: Callable[[C], Awaitable[object]]
    """The one application service method that records the event."""

    async def run(self, message: ConsumedMessage) -> None:
        """Parse ``message``, then call the service method once."""
        await self.handle(self.parse(message))


class RoutingTable:
    """All routes of the consumer, by topic. It is the single handler given to the processor."""

    def __init__(self, routes: Sequence[RunnableRoute]) -> None:
        """Register ``routes``.

        Raises:
            ValueError: if two routes share a topic.
        """
        self._routes: dict[str, RunnableRoute] = {}
        for route in routes:
            if route.topic in self._routes:
                raise ValueError(f"duplicate route for topic {route.topic}")
            self._routes[route.topic] = route

    @property
    def topics(self) -> tuple[str, ...]:
        """The routed topics, in registration order (the consumer subscribes to these)."""
        return tuple(self._routes)

    async def handle(self, message: ConsumedMessage) -> None:
        """Process ``message`` with the route of its topic.

        Raises:
            InvalidMessageError: if no route exists for the topic (the message is dead-lettered,
                never dropped silently), or if the route's parser rejects the content.
        """
        route = self._routes.get(message.topic)
        if route is None:
            raise InvalidMessageError(f"no route for topic {message.topic}")
        await route.run(message)
