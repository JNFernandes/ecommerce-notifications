"""The authenticated caller of the read API, as established from a verified access token."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from notifications.domain.access import AccessScope, Role


class Principal(BaseModel):
    """Who is calling: the token subject, their customer id and their known roles.

    Built only by an ``AccessTokenVerifier`` from a verified token. Never stored or logged.
    """

    model_config = ConfigDict(frozen=True)

    subject: str = Field(description="The token's ``sub`` claim (the Identity user id).")
    customer_id: UUID | None = Field(
        default=None, description="The token's ``customerId`` claim, when present."
    )
    roles: frozenset[Role] = Field(
        default=frozenset(), description="The token's roles this service knows; others dropped."
    )

    def scope(self) -> AccessScope:
        """Return the notifications this caller may see.

        Raises:
            AccessDeniedError: if the caller has no role that grants access.
        """
        return AccessScope.for_caller(self.roles, self.customer_id)
