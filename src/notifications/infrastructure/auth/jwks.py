"""In-memory cache of the signing keys published by ecommerce-identity (its JWKS endpoint)."""

import asyncio
import logging
import time
from collections.abc import Callable

import httpx
from jwt import PyJWK, PyJWKSet, PyJWTError

from notifications.application.errors import AccessTokenUnavailableError, InvalidAccessTokenError

logger = logging.getLogger(__name__)


class JwksKeyStore:
    """Signing keys by ``kid``, fetched lazily from a JWKS URL and refreshed with a cooldown.

    - Nothing is fetched until a key is first needed.
    - A key is refetched only for an unknown ``kid``, and only when ``cooldown`` seconds have
      passed since the last attempt, successful or not. A burst of unknown ``kid`` values
      therefore causes at most one request.
    - A failed or malformed fetch keeps the cached keys.
    """

    def __init__(
        self,
        url: str,
        client: httpx.AsyncClient,
        *,
        cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Fetch from ``url`` with ``client``; ``clock`` measures the cooldown in seconds."""
        self._url = url
        self._client = client
        self._cooldown = cooldown
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._last_attempt: float | None = None
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> PyJWK:
        """Return the key with this ``kid``, fetching the key set if needed and allowed.

        Raises:
            AccessTokenUnavailableError: if no keys are cached and none could be fetched.
            InvalidAccessTokenError: if keys are cached but none has this ``kid``.
        """
        key = self._keys.get(kid)
        if key is not None:
            return key
        async with self._lock:
            # Another request may have refreshed the keys while this one waited.
            key = self._keys.get(kid)
            if key is None and self._may_fetch():
                await self._refresh()
                key = self._keys.get(kid)
        if key is not None:
            return key
        if not self._keys:
            raise AccessTokenUnavailableError
        raise InvalidAccessTokenError

    def _may_fetch(self) -> bool:
        return self._last_attempt is None or self._clock() - self._last_attempt >= self._cooldown

    async def _refresh(self) -> None:
        """Replace the cached keys with the published ones; keep them if the fetch fails."""
        self._last_attempt = self._clock()
        try:
            response = await self._client.get(self._url)
            response.raise_for_status()
            document = response.json()
            if not isinstance(document, dict):
                raise ValueError("the JWKS document is not a JSON object")
            key_set = PyJWKSet.from_dict(document)
        except (httpx.HTTPError, ValueError, PyJWTError) as exc:
            logger.warning(
                "Could not fetch the signing keys from %s (%d cached): %s: %s",
                self._url,
                len(self._keys),
                type(exc).__name__,
                exc,
            )
            return
        self._keys = {key.key_id: key for key in key_set.keys if key.key_id}
        logger.info("Fetched %d signing keys from %s", len(self._keys), self._url)
