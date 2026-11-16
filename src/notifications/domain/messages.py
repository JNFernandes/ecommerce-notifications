"""Message templates. Texts are deterministic and contain ids and amounts only (Principle IV)."""

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from notifications.domain.errors import InvalidOrderTotalError

_CENTS = Decimal("0.01")


def require_valid_total(amount: Decimal) -> Decimal:
    """Return ``amount`` if it is a finite, non-negative order total.

    Raises:
        InvalidOrderTotalError: if the amount is negative, infinite or NaN.
    """
    if not amount.is_finite() or amount < 0:
        raise InvalidOrderTotalError(f"order total must be a finite amount >= 0, got {amount}")
    return amount


def format_amount(amount: Decimal) -> str:
    """Write ``amount`` with two decimals (rounded half up), without exponent or currency."""
    return format(require_valid_total(amount).quantize(_CENTS, rounding=ROUND_HALF_UP), "f")


def order_received_message(order_id: UUID, total_amount: Decimal) -> str:
    """Text of the ``ORDER_RECEIVED`` notification."""
    return f"Your order {order_id} for {format_amount(total_amount)} was received."
