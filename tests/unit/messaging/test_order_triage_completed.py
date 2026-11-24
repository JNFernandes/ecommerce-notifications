"""Tests for parsing ``ai.order-triage-completed`` messages."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from notifications.application.record_order_failure_explanation import (
    OrderTriageCompletedCommand,
)
from notifications.domain.triage import SuggestedAction, TriageSource
from notifications.messaging.messages import InvalidMessageError
from notifications.messaging.order_triage_completed import parse_order_triage_completed
from tests.support.events import MISSING, order_triage_payload, to_json_bytes
from tests.support.fakes import consumed_message


def _parse(**overrides: object) -> OrderTriageCompletedCommand:
    payload = order_triage_payload(**overrides)
    return parse_order_triage_completed(consumed_message(value=to_json_bytes(payload)))


def _reason(**overrides: object) -> str:
    with pytest.raises(InvalidMessageError) as raised:
        _parse(**overrides)
    return raised.value.reason


def test_should_map_a_valid_result_to_the_command() -> None:
    payload = order_triage_payload()

    command = parse_order_triage_completed(consumed_message(value=to_json_bytes(payload)))

    assert command.event_id == UUID(str(payload["eventId"]))
    assert command.order_id == UUID(str(payload["orderId"]))
    assert command.explanation == payload["explanation"]
    assert command.suggested_action is SuggestedAction.RESTOCK
    assert command.source is TriageSource.LLM
    assert command.confidence == Decimal("0.92")
    assert command.prompt_version == "triage.v1"


def test_should_accept_a_fallback_result_without_a_model_and_ignore_unknown_fields() -> None:
    command = _parse(source="FALLBACK_TEMPLATE", modelId=None, confidence=1, extra={"a": 1})

    assert command.source is TriageSource.FALLBACK_TEMPLATE
    assert command.confidence == Decimal(1)


def test_should_pass_the_explanation_on_unchanged_for_the_domain_to_clean() -> None:
    command = _parse(explanation=" two\nlines\x00 ")

    assert command.explanation == " two\nlines\x00 "


@pytest.mark.parametrize(
    "field",
    [
        "eventId",
        "aggregateId",
        "occurredAt",
        "version",
        "orderId",
        "cause",
        "explanation",
        "suggestedAction",
        "confidence",
        "promptVersion",
        "modelId",
        "source",
    ],
)
def test_should_reject_a_result_without_a_required_field(field: str) -> None:
    assert f"{field}: Field required" in _reason(**{field: MISSING})


@pytest.mark.parametrize("version", [2, 0, "1", True, Decimal("1.0")])
def test_should_reject_an_unknown_or_mistyped_version(version: object) -> None:
    assert "version" in _reason(version=version)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cause", "OUT_OF_MONEY"),
        ("cause", 1),
        ("suggestedAction", "REFUND"),
        ("suggestedAction", "restock"),
        ("source", "HUMAN"),
        ("confidence", Decimal("1.01")),
        ("confidence", Decimal("-0.1")),
        ("confidence", "0.9"),
        ("confidence", True),
        ("explanation", ""),
        ("explanation", "x" * 1001),
        ("explanation", 42),
        ("promptVersion", ""),
        ("modelId", 7),
    ],
)
def test_should_reject_a_value_that_breaks_the_contract(field: str, value: object) -> None:
    assert field in _reason(**{field: value})


def test_should_reject_an_order_id_that_differs_from_the_aggregate_id() -> None:
    assert "orderId must equal aggregateId" in _reason(orderId=str(uuid4()))


def test_should_reject_a_result_that_is_not_json() -> None:
    with pytest.raises(InvalidMessageError, match="not valid JSON"):
        parse_order_triage_completed(consumed_message(value=b"{not json"))
