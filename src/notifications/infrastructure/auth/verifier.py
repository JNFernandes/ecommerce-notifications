"""Local verification of ecommerce-identity RS256 access tokens (the ``AccessTokenVerifier``)."""

import logging
from typing import Protocol
from uuid import UUID

import jwt
from jwt import PyJWK, PyJWTError

from notifications.application.auth import Principal
from notifications.application.errors import InvalidAccessTokenError
from notifications.domain.access import Role

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
"""The only accepted signing algorithm; anything else (``none``, HS256, ...) is rejected."""

REQUIRED_CLAIMS = ["exp", "iat", "sub", "iss", "aud"]
"""Claims every access token must carry."""

_KNOWN_ROLES = frozenset(Role)


class SigningKeys(Protocol):
    """Source of the signing keys by ``kid`` (``JwksKeyStore`` in production)."""

    async def get(self, kid: str) -> PyJWK:
        """Return the key with this ``kid``; raise the application token errors otherwise."""
        ...


class _Rejected(Exception):
    """Internal: a reason to reject a token, logged at DEBUG and never returned."""


class JwtAccessTokenVerifier:
    """Verifies RS256 access tokens with Identity's published keys and builds the ``Principal``.

    The order of checks matters: the header (``alg`` RS256 and a ``kid``) is checked before any
    key is looked up, so a forged header never causes a JWKS fetch. Every rejection raises the
    same ``InvalidAccessTokenError``; the cause is logged at DEBUG, never the token.
    """

    def __init__(
        self, keys: SigningKeys, *, issuer: str, audience: str, leeway_seconds: int
    ) -> None:
        """Check the ``iss`` and ``aud`` claims and allow ``leeway_seconds`` of clock skew."""
        self._keys = keys
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds

    async def verify(self, token: str) -> Principal:
        """Return the principal of a valid ``token``.

        Raises:
            InvalidAccessTokenError: if the token is malformed or fails any check.
            AccessTokenUnavailableError: if no signing keys can be obtained.
        """
        try:
            kid = self._key_id(token)
            key = await self._keys.get(kid)
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                issuer=self._issuer,
                audience=self._audience,
                leeway=self._leeway,
                options={"require": REQUIRED_CLAIMS},
            )
            return _principal(claims)
        except (PyJWTError, _Rejected) as exc:
            logger.debug("Access token rejected: %s: %s", type(exc).__name__, exc)
            raise InvalidAccessTokenError from None

    @staticmethod
    def _key_id(token: str) -> str:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != ALGORITHM:
            raise _Rejected(f"algorithm {header.get('alg')!r} is not {ALGORITHM}")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise _Rejected("no kid in the header")
        return kid


def _principal(claims: dict[str, object]) -> Principal:
    """Build the principal from verified claims; unknown roles are dropped."""
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject:
        raise _Rejected("sub is not a non-empty string")
    raw_roles = claims.get("roles")
    roles = (
        frozenset(Role(r) for r in raw_roles if isinstance(r, str) and r in _KNOWN_ROLES)
        if isinstance(raw_roles, list)
        else frozenset()
    )
    return Principal(subject=subject, customer_id=_customer_id(claims), roles=roles)


def _customer_id(claims: dict[str, object]) -> UUID | None:
    value = claims.get("customerId")
    if value is None:
        return None
    if not isinstance(value, str):
        raise _Rejected("customerId is not a string")
    try:
        return UUID(value)
    except ValueError:
        raise _Rejected("customerId is not a UUID") from None
