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


PAYMENT_REFUNDED_TOPIC = "payments.payment-refunded"


def payment_refunded_payload(**overrides: object) -> dict[str, object]:
    """Return a valid ``payments.payment-refunded`` JSON object, as Payments publishes it.

    ``aggregateId`` is the payment id and always differs from ``orderId``. Timestamps use the
    ``+00:00`` offset that .NET ``DateTimeOffset`` writes. ``MISSING`` removes a key.
    """
    now = datetime.now(UTC).isoformat()
    payload: dict[str, object] = {
        "eventId": str(uuid4()),
        "occurredAt": now,
        "aggregateId": str(uuid4()),
        "version": 1,
        "orderId": str(uuid4()),
        "amount": Decimal("129.90"),
        "currency": "EUR",
        "refundedAt": now,
    }
    for key, value in overrides.items():
        if value is MISSING:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload


ORDER_TRIAGE_COMPLETED_TOPIC = "ai.order-triage-completed"


def order_triage_payload(**overrides: object) -> dict[str, object]:
    """Return a valid ``ai.order-triage-completed`` JSON object, as ecommerce-ai publishes it.

    ``aggregateId`` and ``orderId`` are the same order id; pass ``order_id=...`` to set both.
    ``MISSING`` removes a key.
    """
    order_id = str(overrides.pop("order_id", uuid4()))
    payload: dict[str, object] = {
        "eventId": str(uuid4()),
        "aggregateId": order_id,
        "occurredAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "version": 1,
        "orderId": order_id,
        "cause": "INSUFFICIENT_STOCK",
        "explanation": (
            "Stock for product SKU-002 was short by 3 units, so nothing was reserved and the "
            "order was cancelled."
        ),
        "suggestedAction": "RESTOCK",
        "confidence": Decimal("0.92"),
        "promptVersion": "triage.v1",
        "modelId": "claude-haiku-5-5",
        "source": "LLM",
    }
    for key, value in overrides.items():
        if value is MISSING:
            payload.pop(key, None)
        else:
            payload[key] = value
    return payload
