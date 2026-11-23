"""Tests for the RS256 access token verifier, with keys served by a local JWKS endpoint."""

import logging
import time
from collections.abc import AsyncIterator, Callable, Iterator
from uuid import UUID

import httpx
import pytest

from notifications.application.auth import Principal
from notifications.application.errors import AccessTokenUnavailableError, InvalidAccessTokenError
from notifications.domain.access import Role
from notifications.infrastructure.auth.jwks import JwksKeyStore
from notifications.infrastructure.auth.verifier import JwtAccessTokenVerifier
from tests.support.tokens import AUDIENCE, ISSUER, JwksServer, TokenIssuer

CUSTOMER = UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
LEEWAY = 5


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


def _verifier(url: str, client: httpx.AsyncClient) -> JwtAccessTokenVerifier:
    return JwtAccessTokenVerifier(
        JwksKeyStore(url, client), issuer=ISSUER, audience=AUDIENCE, leeway_seconds=LEEWAY
    )


@pytest.fixture
def verifier(server: JwksServer, client: httpx.AsyncClient) -> JwtAccessTokenVerifier:
    return _verifier(server.url, client)


async def test_should_return_the_principal_of_a_valid_customer_token(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer
) -> None:
    token = issuer.sign({"sub": "user-1", "roles": ["CUSTOMER"], "customerId": CUSTOMER})

    principal = await verifier.verify(token)

    assert principal == Principal(
        subject="user-1", customer_id=CUSTOMER, roles=frozenset({Role.CUSTOMER})
    )


async def test_should_return_both_roles_and_no_customer_when_the_token_has_none(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer
) -> None:
    principal = await verifier.verify(issuer.sign({"roles": ["CUSTOMER", "ADMIN"]}))

    assert principal.roles == {Role.CUSTOMER, Role.ADMIN}
    assert principal.customer_id is None


async def test_should_accept_a_token_expired_within_the_clock_tolerance(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer
) -> None:
    token = issuer.sign({"exp": int(time.time()) - 2})

    assert (await verifier.verify(token)).subject


def _now() -> int:
    return int(time.time())


@pytest.mark.parametrize(
    ("claims", "omit"),
    [
        pytest.param({"exp": _now() - 60}, (), id="expired"),
        pytest.param({"iss": "http://evil.example"}, (), id="wrong-issuer"),
        pytest.param({"aud": "another-service"}, (), id="wrong-audience"),
        pytest.param({}, ("exp",), id="no-exp"),
        pytest.param({}, ("iat",), id="no-iat"),
        pytest.param({}, ("sub",), id="no-sub"),
        pytest.param({}, ("iss",), id="no-iss"),
        pytest.param({}, ("aud",), id="no-aud"),
        pytest.param({"sub": ""}, (), id="empty-sub"),
        pytest.param({"sub": 42}, (), id="numeric-sub"),
        pytest.param({"customerId": "not-a-uuid"}, (), id="customer-id-not-a-uuid"),
        pytest.param({"customerId": 42}, (), id="customer-id-not-a-string"),
    ],
)
async def test_should_reject_a_token_with_bad_claims(
    verifier: JwtAccessTokenVerifier,
    issuer: TokenIssuer,
    claims: dict[str, object],
    omit: tuple[str, ...],
) -> None:
    with pytest.raises(InvalidAccessTokenError):
        await verifier.verify(issuer.sign(claims, omit=omit))


async def test_should_reject_a_token_signed_by_another_key_with_the_same_kid(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer
) -> None:
    impostor = TokenIssuer(kid=issuer.kid)

    with pytest.raises(InvalidAccessTokenError):
        await verifier.verify(impostor.sign())


@pytest.mark.parametrize(
    "forge",
    [
        pytest.param(lambda i: i.unsigned(), id="alg-none"),
        pytest.param(lambda i: i.hs256_with_public_key(), id="hs256-with-public-key"),
        pytest.param(lambda i: i.sign(headers={"kid": None}), id="no-kid"),
        pytest.param(lambda i: i.sign(headers={"kid": ""}), id="empty-kid"),
        pytest.param(lambda i: i.sign(headers={"alg": "RS512"}), id="other-alg-in-header"),
        pytest.param(lambda i: "not.a.token", id="garbage"),
        pytest.param(lambda i: "", id="empty"),
    ],
)
async def test_should_reject_a_forged_or_malformed_token_without_fetching_keys(
    verifier: JwtAccessTokenVerifier,
    server: JwksServer,
    issuer: TokenIssuer,
    forge: Callable[[TokenIssuer], str],
) -> None:
    token = forge(issuer)

    with pytest.raises(InvalidAccessTokenError):
        await verifier.verify(token)

    assert server.requests == 0


async def test_should_reject_a_token_with_an_unknown_kid(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer
) -> None:
    await verifier.verify(issuer.sign())  # caches the keys

    with pytest.raises(InvalidAccessTokenError):
        await verifier.verify(TokenIssuer(kid="unknown").sign())


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (["ADMIN", "SUPPORT", 7, None], {Role.ADMIN}),
        (["SUPPORT"], set()),
        ("ADMIN", set()),
        ({"ADMIN": True}, set()),
        (None, set()),
    ],
)
async def test_should_keep_only_known_roles_from_a_list(
    verifier: JwtAccessTokenVerifier,
    issuer: TokenIssuer,
    roles: object,
    expected: set[Role],
) -> None:
    principal = await verifier.verify(issuer.sign({"roles": roles}))

    assert principal.roles == expected


async def test_should_accept_a_token_without_roles(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer
) -> None:
    assert (await verifier.verify(issuer.sign(omit=("roles",)))).roles == frozenset()


async def test_should_raise_unavailable_when_no_keys_can_be_fetched(
    client: httpx.AsyncClient, issuer: TokenIssuer
) -> None:
    verifier = _verifier("http://127.0.0.1:1/.well-known/jwks.json", client)

    with pytest.raises(AccessTokenUnavailableError):
        await verifier.verify(issuer.sign())


async def test_should_never_log_the_token_when_rejecting_it(
    verifier: JwtAccessTokenVerifier, issuer: TokenIssuer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    token = issuer.sign({"exp": _now() - 60})

    with pytest.raises(InvalidAccessTokenError) as raised:
        await verifier.verify(token)

    assert "Access token rejected" in caplog.text
    assert token not in caplog.text
    assert token.split(".")[1] not in caplog.text
    assert str(raised.value) == "invalid or missing access token"
    assert raised.value.__cause__ is None
