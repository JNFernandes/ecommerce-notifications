"""Parser for ``payments.payment-refunded``.

Contract: ``ecommerce-infra/docs/events.md`` (Payments events), checked against the producer's
``PaymentRefundedEvent`` (ecommerce-payments): ``aggregateId`` is the payment id; the order is
``orderId``; ``amount`` is a JSON number; timestamps are ISO 8601 with an offset.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import AwareDatetime, BeforeValidator, Field

from notifications.application.record_payment_refunded import PaymentRefundedCommand
from notifications.messaging.events import (
    EventEnvelope,
    JsonUuid,
    json_number,
    json_string,
    parse_event,
)
from notifications.messaging.messages import ConsumedMessage


class PaymentRefundedEvent(EventEnvelope):
    """A ``payments.payment-refunded`` event: a processed payment was refunded because its order
    was cancelled. It has no customer; the customer comes from the order context."""

    order_id: JsonUuid = Field(alias="orderId")
    """The order the refunded payment was for. This, not ``aggregateId``, identifies the order."""

    amount: Annotated[Decimal, BeforeValidator(json_number), Field(ge=0)]
    """Amount refunded (the full amount of the payment), as an exact decimal."""

    currency: Annotated[str, BeforeValidator(json_string), Field(pattern=r"^[A-Z]{3}$")]
    """ISO 4217 currency code of the refund."""

    refunded_at: Annotated[AwareDatetime, BeforeValidator(json_string)] = Field(alias="refundedAt")
    """When the payment was refunded, ISO 8601 with a timezone."""

    def to_command(self) -> PaymentRefundedCommand:
        """Map the event to the application command, identifying the order by ``orderId``."""
        return PaymentRefundedCommand(
            event_id=self.event_id,
            order_id=self.order_id,
            amount=self.amount,
            currency=self.currency,
        )


def parse_payment_refunded(message: ConsumedMessage) -> PaymentRefundedCommand:
    """Decode and validate a ``payments.payment-refunded`` message into the application command.

    Raises:
        InvalidMessageError: if the value is not a JSON object or breaks the contract.
    """
    return parse_event(message, PaymentRefundedEvent).to_command()
