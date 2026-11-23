"""Tests for the JWKS key store, against a real local JWKS endpoint."""

import asyncio
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from notifications.application.errors import AccessTokenUnavailableError, InvalidAccessTokenError
from notifications.infrastructure.auth.jwks import JwksKeyStore
from tests.support.tokens import JwksServer, TokenIssuer

UNREACHABLE_URL = "http://127.0.0.1:1/.well-known/jwks.json"


class FakeClock:
    """Monotonic clock moved by hand."""

    def __init__(self) -> None:
        """Start at zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.now


@pytest.fixture(scope="module")
def issuer() -> TokenIssuer:
    return TokenIssuer(kid="key-1")


@pytest.fixture
def server(issuer: TokenIssuer) -> Iterator[JwksServer]:
    with JwksServer([issuer.public_jwk()]) as server:
        yield server


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=3) as client:
        yield client


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def _store(url: str, client: httpx.AsyncClient, clock: FakeClock) -> JwksKeyStore:
    return JwksKeyStore(url, client, cooldown=60.0, clock=clock)


async def test_should_not_fetch_until_a_key_is_needed(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock
) -> None:
    _store(server.url, client, clock)

    assert server.requests == 0


async def test_should_fetch_once_and_serve_later_lookups_from_the_cache(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    store = _store(server.url, client, clock)

    first = await store.get(issuer.kid)
    clock.now = 3600.0
    second = await store.get(issuer.kid)

    assert first is second
    assert first.key_id == issuer.kid
    assert server.requests == 1


async def test_should_fetch_once_when_a_burst_of_requests_needs_the_first_key(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    store = _store(server.url, client, clock)

    keys = await asyncio.gather(*(store.get(issuer.kid) for _ in range(20)))

    assert {key.key_id for key in keys} == {issuer.kid}
    assert server.requests == 1


async def test_should_reject_an_unknown_kid_without_refetching_within_the_cooldown(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    store = _store(server.url, client, clock)
    await store.get(issuer.kid)
    clock.now = 59.9

    with pytest.raises(InvalidAccessTokenError):
        await store.get("unknown")

    assert server.requests == 1


async def test_should_fetch_once_when_a_burst_of_unknown_kids_arrives_after_the_cooldown(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    store = _store(server.url, client, clock)
    await store.get(issuer.kid)
    clock.now = 60.0

    results = await asyncio.gather(
        *(store.get(f"unknown-{i}") for i in range(20)), return_exceptions=True
    )

    assert all(isinstance(result, InvalidAccessTokenError) for result in results)
    assert server.requests == 2


async def test_should_accept_a_rotated_key_after_one_refresh(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    store = _store(server.url, client, clock)
    await store.get(issuer.kid)
    rotated = TokenIssuer(kid="key-2")
    server.keys = [rotated.public_jwk()]
    clock.now = 61.0

    key = await store.get(rotated.kid)

    assert key.key_id == rotated.kid
    assert server.requests == 2
    with pytest.raises(InvalidAccessTokenError):
        await store.get(issuer.kid)  # the retired key was dropped with the refresh


async def test_should_raise_unavailable_when_nothing_is_cached_and_identity_is_unreachable(
    client: httpx.AsyncClient, clock: FakeClock
) -> None:
    store = _store(UNREACHABLE_URL, client, clock)

    with pytest.raises(AccessTokenUnavailableError):
        await store.get("key-1")


async def test_should_not_retry_a_failing_identity_within_the_cooldown(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    server.serve_malformed()
    store = _store(server.url, client, clock)
    with pytest.raises(AccessTokenUnavailableError):
        await store.get(issuer.kid)

    with pytest.raises(AccessTokenUnavailableError):
        await store.get(issuer.kid)

    assert server.requests == 1


async def test_should_treat_a_malformed_key_set_as_a_failed_fetch(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    server.serve_malformed()
    store = _store(server.url, client, clock)

    with pytest.raises(AccessTokenUnavailableError):
        await store.get(issuer.kid)


@pytest.mark.parametrize("keys", [[], [{"kty": "RSA", "kid": "broken"}]])
async def test_should_treat_a_key_set_without_usable_keys_as_a_failed_fetch(
    server: JwksServer,
    client: httpx.AsyncClient,
    clock: FakeClock,
    keys: list[dict[str, object]],
) -> None:
    server.keys = keys
    store = _store(server.url, client, clock)

    with pytest.raises(AccessTokenUnavailableError):
        await store.get("broken")


async def test_should_treat_a_json_body_that_is_not_an_object_as_a_failed_fetch(
    clock: FakeClock,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "a", "key", "set"])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mocked:
        store = _store("http://identity.test/jwks.json", mocked, clock)

        with pytest.raises(AccessTokenUnavailableError):
            await store.get("key-1")


async def test_should_treat_an_error_status_as_a_failed_fetch(clock: FakeClock) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as mocked:
        store = _store("http://identity.test/jwks.json", mocked, clock)

        with pytest.raises(AccessTokenUnavailableError):
            await store.get("key-1")


async def test_should_keep_serving_cached_keys_when_identity_goes_down(
    server: JwksServer, client: httpx.AsyncClient, clock: FakeClock, issuer: TokenIssuer
) -> None:
    store = _store(server.url, client, clock)
    await store.get(issuer.kid)
    server.serve_malformed()
    clock.now = 120.0

    with pytest.raises(InvalidAccessTokenError):
        await store.get("unknown")  # the refresh fails, but keys are cached: 401, not 503

    assert (await store.get(issuer.kid)).key_id == issuer.kid
    assert server.requests == 2
