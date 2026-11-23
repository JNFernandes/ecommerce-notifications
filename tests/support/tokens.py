"""Test access tokens: a local RSA token issuer and a local JWKS endpoint.

They stand in for ecommerce-identity, so no test needs the Identity service. ``TokenIssuer`` signs
RS256 tokens (and a few forged ones); ``JwksServer`` serves its public keys over real HTTP and
counts the requests, which proves the key store's cache and cooldown.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Iterable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ISSUER = "http://localhost:3001"
"""Default ``iss`` of the test tokens; equals the ``JWT_ISSUER`` default."""

AUDIENCE = "ecommerce"
"""Default ``aud`` of the test tokens; equals the ``JWT_AUDIENCE`` default."""

type Jwk = dict[str, object]


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _segment(value: Mapping[str, object]) -> str:
    return _b64url(json.dumps(value, separators=(",", ":")).encode())


class TokenIssuer:
    """Signs access tokens with its own RSA-2048 key, identified by ``kid``."""

    def __init__(self, kid: str | None = None) -> None:
        """Generate a fresh key pair; ``kid`` defaults to a random value."""
        self.kid = kid if kid is not None else f"test-{uuid4().hex[:8]}"
        self._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def public_jwk(self) -> Jwk:
        """Return the public key as a JWK, as ecommerce-identity publishes it."""
        jwk: Jwk = RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        return {**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}

    def jwks(self) -> dict[str, list[Jwk]]:
        """Return a key set holding only this issuer's public key."""
        return {"keys": [self.public_jwk()]}

    def claims(
        self, overrides: Mapping[str, object] | None = None, *, omit: Iterable[str] = ()
    ) -> dict[str, object]:
        """Return valid default claims, with ``overrides`` applied and the ``omit`` names removed.

        Defaults: a random ``sub``, the default issuer and audience, ``iat`` now, ``exp`` in five
        minutes and ``roles`` ``["CUSTOMER"]``. Add ``customerId`` through ``overrides`` (a
        ``UUID`` value is turned into a string).
        """
        now = int(time.time())
        payload: dict[str, object] = {
            "sub": str(uuid4()),
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + 300,
            "roles": ["CUSTOMER"],
        }
        for name, value in (overrides or {}).items():
            payload[name] = str(value) if isinstance(value, UUID) else value
        for name in omit:
            payload.pop(name, None)
        return payload

    def sign(
        self,
        claims: Mapping[str, object] | None = None,
        *,
        omit: Iterable[str] = (),
        headers: Mapping[str, object] | None = None,
    ) -> str:
        """Return an RS256 token with these claim and header overrides (see ``claims``).

        A header override of ``None`` removes that header (for example ``{"kid": None}``).
        """
        header: dict[str, object] = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        header.update(headers or {})
        header = {k: v for k, v in header.items() if v is not None}
        signing_input = f"{_segment(header)}.{_segment(self.claims(claims, omit=omit))}"
        signature = RSAAlgorithm(RSAAlgorithm.SHA256).sign(
            signing_input.encode(), self._private_key
        )
        return f"{signing_input}.{_b64url(signature)}"

    def customer_token(self, customer_id: UUID, claims: Mapping[str, object] | None = None) -> str:
        """Return a valid CUSTOMER token for ``customer_id``."""
        return self.sign({"roles": ["CUSTOMER"], "customerId": customer_id, **(claims or {})})

    def admin_token(self, claims: Mapping[str, object] | None = None) -> str:
        """Return a valid ADMIN token (no customer)."""
        return self.sign({"roles": ["ADMIN"], **(claims or {})})

    def unsigned(self, claims: Mapping[str, object] | None = None) -> str:
        """Return a token with ``alg: none`` and an empty signature (an ``alg=none`` attack)."""
        header = {"alg": "none", "typ": "JWT", "kid": self.kid}
        return f"{_segment(header)}.{_segment(self.claims(claims))}."

    def hs256_with_public_key(self, claims: Mapping[str, object] | None = None) -> str:
        """Return an HS256 token whose secret is the PEM public key (key confusion attack)."""
        secret = self._private_key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        header = {"alg": "HS256", "typ": "JWT", "kid": self.kid}
        signing_input = f"{_segment(header)}.{_segment(self.claims(claims))}"
        signature = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
        return f"{signing_input}.{_b64url(signature)}"


class JwksServer:
    """A local JWKS endpoint on ``127.0.0.1`` with a random port, run in a daemon thread.

    Serves ``keys`` (mutable, for rotation) or, after ``serve_malformed``, a body that is not a
    key set. ``requests`` counts the requests served. Use it as a context manager.
    """

    PATH = "/.well-known/jwks.json"

    def __init__(self, keys: Iterable[Jwk] = ()) -> None:
        """Serve ``keys`` once started."""
        self.keys: list[Jwk] = list(keys)
        self.malformed = False
        self._requests = 0
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        """The JWKS URL to configure as ``IDENTITY_JWKS_URL``."""
        return f"http://127.0.0.1:{self._server.server_address[1]}{self.PATH}"

    @property
    def requests(self) -> int:
        """Number of requests served so far."""
        with self._lock:
            return self._requests

    def serve_malformed(self) -> None:
        """Answer every later request with a 200 body that is not a key set."""
        self.malformed = True

    def start(self) -> Self:
        """Start serving in a daemon thread."""
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        """Stop serving and close the socket; later requests are refused."""
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join()
            self._thread = None
        self._server.server_close()

    def __enter__(self) -> Self:
        """Start serving."""
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop serving."""
        self.stop()

    def _body(self) -> bytes:
        with self._lock:
            self._requests += 1
            if self.malformed:
                return b'{"keys": "not a list"'
            return json.dumps({"keys": self.keys}).encode()

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = owner._body()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                """Stay quiet: the test output is not the place for access logs."""

        return Handler
