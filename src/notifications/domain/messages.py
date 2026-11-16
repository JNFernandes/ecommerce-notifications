"""Message templates. Texts are deterministic and contain ids and amounts only (Principle IV)."""

import re
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from notifications.domain.errors import (
    InvalidAmountError,
    InvalidCurrencyError,
    InvalidOrderTotalError,
)

_CENTS = Decimal("0.01")
_LINE_BREAKS_AND_TABS = re.compile(r"[\r\n\t]+")

_CURRENCY_CODE = re.compile(r"[A-Z]{3}")

MAX_REASON_LENGTH = 500
"""Longest cancellation reason shown in a message; longer reasons are cut and end with ``…``."""


def require_valid_amount(amount: Decimal) -> Decimal:
    """Return ``amount`` if it is a finite, non-negative amount of money.

    Raises:
        InvalidAmountError: if the amount is negative, infinite or NaN.
    """
    if not amount.is_finite() or amount < 0:
        raise InvalidAmountError(f"amount must be a finite amount >= 0, got {amount}")
    return amount


def require_valid_total(amount: Decimal) -> Decimal:
    """Return ``amount`` if it is a finite, non-negative order total.

    Raises:
        InvalidOrderTotalError: if the amount is negative, infinite or NaN.
    """
    if not amount.is_finite() or amount < 0:
        raise InvalidOrderTotalError(f"order total must be a finite amount >= 0, got {amount}")
    return amount


def format_amount(amount: Decimal) -> str:
    """Write ``amount`` with two decimals (rounded half up), without exponent or currency.

    Raises:
        InvalidAmountError: if the amount is negative, infinite or NaN.
    """
    return format(require_valid_amount(amount).quantize(_CENTS, rounding=ROUND_HALF_UP), "f")


def order_received_message(order_id: UUID, total_amount: Decimal) -> str:
    """Text of the ``ORDER_RECEIVED`` notification.

    Raises:
        InvalidOrderTotalError: if the total is negative, infinite or NaN.
    """
    amount = format_amount(require_valid_total(total_amount))
    return f"Your order {order_id} for {amount} was received."


def order_confirmed_message(order_id: UUID) -> str:
    """Text of the ``ORDER_CONFIRMED`` notification."""
    return f"Your order {order_id} was confirmed."


def normalise_reason(raw: str | None) -> str | None:
    """Return the cancellation reason as shown to the customer, or ``None`` for "no reason".

    Rules, in order: each run of line breaks and tabs becomes one space; surrounding whitespace
    is trimmed; one trailing period is removed (the template adds its own); an empty result means
    no reason; a reason longer than ``MAX_REASON_LENGTH`` characters is cut and ends with ``…``.
    """
    if raw is None:
        return None
    reason = _LINE_BREAKS_AND_TABS.sub(" ", raw).strip()
    reason = reason.removesuffix(".").strip()
    if not reason:
        return None
    if len(reason) > MAX_REASON_LENGTH:
        return reason[:MAX_REASON_LENGTH] + "…"
    return reason


def order_cancelled_message(order_id: UUID, reason: str | None) -> str:
    """Text of the ``ORDER_CANCELLED`` notification, with the reason when there is one."""
    shown = normalise_reason(reason)
    if shown is None:
        return f"Your order {order_id} was cancelled."
    return f"Your order {order_id} was cancelled: {shown}."


def require_currency_code(code: str) -> str:
    """Return ``code`` if it is a three-letter uppercase ISO 4217 code.

    Raises:
        InvalidCurrencyError: otherwise.
    """
    if not _CURRENCY_CODE.fullmatch(code):
        raise InvalidCurrencyError(f"currency must be three uppercase letters, got {code!r}")
    return code


def payment_refunded_message(order_id: UUID, amount: Decimal, currency: str) -> str:
    """Text of the ``PAYMENT_REFUNDED`` notification: amount with two decimals, then currency.

    Raises:
        InvalidAmountError: if the amount is negative, infinite or NaN.
        InvalidCurrencyError: if the currency is not a three-letter uppercase code.
    """
    shown = f"{format_amount(amount)} {require_currency_code(currency)}"
    return f"Your payment of {shown} for order {order_id} was refunded."
