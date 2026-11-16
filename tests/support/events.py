"""Builders for ``orders.order-placed`` payloads, as Orders publishes them."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final
from uuid import uuid4


class _Missing:
    """Sentinel type: an override with this value removes the key from the payload."""


MISSING: Final = _Missing()


def order_placed_payload(**overrides: object) -> dict[str, object]:
    """Return a valid ``orders.order-placed`` JSON object, with ``overrides`` applied."""
    payload: dict[str, object] = {
        "eventId": str(uuid4()),
        "aggregateId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "version": 1,
        "customerId": str(uuid4()),
        "items": [{"productId": "SKU-001", "quantity": 2, "unitPrice": Decimal("64.95")}],
        "totalAmount": Decimal("129.90"),
    }
    for key, value in overrides.items():
        if value is MISSING:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


def to_json_bytes(payload: object) -> bytes:
    """Serialise to UTF-8 JSON, writing every ``Decimal`` as a JSON number without rounding."""
    decimals: list[Decimal] = []

    def _replace(value: object) -> object:
        if isinstance(value, Decimal):
            decimals.append(value)
            return f"__decimal_{len(decimals) - 1}__"
        if isinstance(value, dict):
            return {key: _replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_replace(item) for item in value]
        return value

    text = json.dumps(_replace(payload))
    for index, value in enumerate(decimals):
        text = text.replace(f'"__decimal_{index}__"', str(value))
    return text.encode()


ORDER_CONFIRMED_TOPIC = "orders.order-confirmed"
ORDER_CANCELLED_TOPIC = "orders.order-cancelled"


def order_status_payload(**overrides: object) -> dict[str, object]:
    """Return a valid ``orders.order-confirmed`` / ``orders.order-cancelled`` JSON object.

    Both events share the envelope plus ``customerId``; add ``reason=...`` for a cancellation.
    An override with the value ``MISSING`` removes the key.
    """
    payload: dict[str, object] = {
        "eventId": str(uuid4()),
        "aggregateId": str(uuid4()),
        "occurredAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "version": 1,
        "customerId": str(uuid4()),
    }
    for key, value in overrides.items():
        if value is MISSING:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload
