"""Tests for the topic routing table."""

import re

import pytest

from notifications.messaging.messages import ConsumedMessage, InvalidMessageError
from notifications.messaging.routing import Route, RoutingTable
from tests.support.fakes import consumed_message


class RecordingService:
    """Stands in for an application service method; records the commands it receives."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    async def record(self, command: str) -> None:
        self.commands.append(command)


def _parse_value(message: ConsumedMessage) -> str:
    return (message.value or b"").decode()


def _reject(message: ConsumedMessage) -> str:
    raise InvalidMessageError(f"invalid payload on {message.topic}")


async def test_should_route_a_message_to_the_route_of_its_topic_only() -> None:
    placed, confirmed = RecordingService(), RecordingService()
    table = RoutingTable(
        [
            Route("orders.order-placed", _parse_value, placed.record),
            Route("orders.order-confirmed", _parse_value, confirmed.record),
        ]
    )

    await table.handle(consumed_message(topic="orders.order-confirmed", value=b"confirm-1"))

    assert confirmed.commands == ["confirm-1"]
    assert placed.commands == []


async def test_should_not_call_the_service_when_the_parser_rejects_the_message() -> None:
    service = RecordingService()
    table = RoutingTable([Route("orders.order-placed", _reject, service.record)])

    with pytest.raises(InvalidMessageError, match="invalid payload"):
        await table.handle(consumed_message(topic="orders.order-placed"))

    assert service.commands == []


async def test_should_reject_a_message_on_a_topic_without_a_route() -> None:
    table = RoutingTable([Route("orders.order-placed", _parse_value, RecordingService().record)])

    with pytest.raises(InvalidMessageError, match=re.escape("no route for topic orders.unknown")):
        await table.handle(consumed_message(topic="orders.unknown"))


def test_should_refuse_two_routes_for_the_same_topic() -> None:
    service = RecordingService()

    with pytest.raises(
        ValueError, match=re.escape("duplicate route for topic orders.order-placed")
    ):
        RoutingTable(
            [
                Route("orders.order-placed", _parse_value, service.record),
                Route("orders.order-placed", _parse_value, service.record),
            ]
        )


def test_should_list_the_topics_in_registration_order() -> None:
    service = RecordingService()
    table = RoutingTable(
        [
            Route("orders.order-cancelled", _parse_value, service.record),
            Route("orders.order-placed", _parse_value, service.record),
        ]
    )

    assert table.topics == ("orders.order-cancelled", "orders.order-placed")
