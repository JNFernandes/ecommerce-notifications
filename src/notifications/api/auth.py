"""Authentication and authorisation dependencies of the HTTP API.

Every route declares its access with exactly one of these (checked at startup by
``route_policy.assert_route_policies``):

- ``PUBLIC``: no token needed (health probes);
- ``require_roles(...)``: a valid bearer token with at least one of the roles.

``ScopeDependency`` turns the caller into the domain ``AccessScope`` that the router hands to the
application service. Errors raised here are mapped to 401/403/503 in ``api.errors``.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from notifications.application.auth import Principal
from notifications.application.errors import InvalidAccessTokenError
from notifications.application.ports import AccessTokenVerifier
from notifications.domain.access import AccessDeniedError, AccessScope, Role

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="bearerAuth",
    bearerFormat="JWT",
    description="RS256 access token issued by ecommerce-identity.",
)
"""The ``bearerAuth`` OpenAPI security scheme; never answers by itself (``auto_error=False``)."""

BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


def get_access_token_verifier(request: Request) -> AccessTokenVerifier:
    """Return the verifier the composition root put on the application state."""
    verifier: AccessTokenVerifier | None = getattr(request.app.state, "access_token_verifier", None)
    if verifier is None:
        raise RuntimeError("AccessTokenVerifier is not configured on the application state")
    return verifier


async def authenticate(
    credentials: BearerCredentials,
    verifier: Annotated[AccessTokenVerifier, Depends(get_access_token_verifier)],
) -> Principal:
    """Return the caller of a request with a valid bearer token.

    Raises:
        InvalidAccessTokenError: if the header is missing, not Bearer, empty or the token is
            invalid.
        AccessTokenUnavailableError: if the token cannot be checked (no signing keys).
    """
    if credentials is None or not credentials.credentials:
        raise InvalidAccessTokenError
    return await verifier.verify(credentials.credentials)


class RoleRequirement:
    """Dependency that admits an authenticated caller with at least one of ``roles``."""

    def __init__(self, roles: tuple[Role, ...]) -> None:
        """Admit callers with any of ``roles`` (kept in order for the OpenAPI ``x-roles``)."""
        if not roles:
            raise ValueError("a role requirement needs at least one role")
        self.roles = roles

    async def __call__(self, principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        """Return the caller, or raise ``AccessDeniedError`` when no role matches."""
        if principal.roles.isdisjoint(self.roles):
            raise AccessDeniedError
        return principal


def require_roles(*roles: Role) -> RoleRequirement:
    """Return a dependency that requires one of ``roles``."""
    return RoleRequirement(roles)


def public_access() -> None:
    """Mark a route as public: no token is read or needed."""


PUBLIC = Depends(public_access)
"""Route dependency declaring public access (see ``route_policy``)."""


def access_scope(principal: Annotated[Principal, Depends(authenticate)]) -> AccessScope:
    """Return the notifications the caller may see.

    Raises:
        AccessDeniedError: if the caller has no role that grants access.
    """
    return principal.scope()


ScopeDependency = Annotated[AccessScope, Depends(access_scope)]
