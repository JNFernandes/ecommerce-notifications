"""Use case: record the "payment refunded" notification (US-03).

``payments.payment-refunded`` carries no customer: the customer is the one of the order context
recorded from ``orders.order-placed`` (ADR 0003).
"""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class PaymentRefundedCommand:
    """The data of a ``payments.payment-refunded`` event that this use case needs."""

    event_id: UUID
    order_id: UUID
    """The event's ``orderId`` (never ``aggregateId``, which is the payment id)."""
    amount: Decimal
    currency: str
