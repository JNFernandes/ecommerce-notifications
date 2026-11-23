"""Who may read which notifications: caller roles and the access scope they grant.

Plain Python (constitution Principle I). The API turns a verified token into roles and an
optional customer id; this module alone decides what that caller may see.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Self
from uuid import UUID

from notifications.domain.errors import DomainError


class Role(StrEnum):
    """Roles carried by ecommerce-identity access tokens that this service understands."""

    CUSTOMER = "CUSTOMER"
    ADMIN = "ADMIN"


class AccessDeniedError(DomainError):
    """The caller is authenticated but has no role that grants access (mapped to 403)."""

    def __init__(self) -> None:
        """Create the error with a generic message: it never names the caller."""
        super().__init__("access denied")


@dataclass(frozen=True, slots=True)
class AccessScope:
    """The notifications a caller may see: one customer's, or every customer's.

    ``customer_id`` is ``None`` for the scope over all customers.
    """

    customer_id: UUID | None

    @classmethod
    def all(cls) -> Self:
        """Return the scope over every customer's notifications."""
        return cls(customer_id=None)

    @classmethod
    def customer(cls, customer_id: UUID) -> Self:
        """Return the scope over the notifications of ``customer_id`` only."""
        return cls(customer_id=customer_id)

    @classmethod
    def for_caller(cls, roles: Iterable[Role], customer_id: UUID | None) -> Self:
        """Return the scope granted to a caller with ``roles`` and ``customer_id``.

        ADMIN sees everything, and wins over CUSTOMER. CUSTOMER with a customer id sees that
        customer's notifications.

        Raises:
            AccessDeniedError: for any other caller (no usable role, or CUSTOMER without a
                customer id).
        """
        granted = frozenset(roles)
        if Role.ADMIN in granted:
            return cls.all()
        if Role.CUSTOMER in granted and customer_id is not None:
            return cls.customer(customer_id)
        raise AccessDeniedError

    @property
    def is_all(self) -> bool:
        """Whether this scope covers every customer."""
        return self.customer_id is None

    def permits_customer(self, requested: UUID | None) -> bool:
        """Whether a request for ``requested``'s notifications (``None``: any) fits this scope."""
        return requested is None or self.customer_id is None or requested == self.customer_id
