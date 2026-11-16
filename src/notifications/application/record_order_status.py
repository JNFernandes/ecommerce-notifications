"""Use cases: record the "order confirmed" and "order cancelled" notifications (US-02)."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class OrderConfirmedCommand:
    """The data of an ``orders.order-confirmed`` event that this use case needs."""

    event_id: UUID
    order_id: UUID
    customer_id: UUID
