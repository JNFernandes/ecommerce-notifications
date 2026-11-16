"""The ``OrderContext`` aggregate: what Notifications knows about an order from its placement.

Later events that only carry the order id (``payments.payment-refunded``, US-03) use it to find
the customer of the order (ADR 0003).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Self
from uuid import UUID

from notifications.domain.messages import require_valid_total
from notifications.domain.time import require_utc_millis


@dataclass(frozen=True, slots=True)
class OrderContext:
    """The customer and total of one order. The first recorded context of an order wins."""

    order_id: UUID
    customer_id: UUID
    total_amount: Decimal
    recorded_at: datetime

    @classmethod
    def from_order_placed(
        cls, *, order_id: UUID, customer_id: UUID, total_amount: Decimal, now: datetime
    ) -> Self:
        """Build the context from an order placement.

        Raises:
            InvalidOrderTotalError: if ``total_amount`` is negative or not finite.
            NaiveTimestampError: if ``now`` has no timezone.
        """
        return cls(
            order_id=order_id,
            customer_id=customer_id,
            total_amount=require_valid_total(total_amount),
            recorded_at=require_utc_millis(now),
        )

    def conflicts_with(self, other: "OrderContext") -> bool:
        """Return ``True`` when ``other`` names a different customer or total for the order.

        Totals are compared as numbers (``129.9`` equals ``129.90``); ``recorded_at`` is
        ignored.
        """
        return self.customer_id != other.customer_id or self.total_amount != other.total_amount
