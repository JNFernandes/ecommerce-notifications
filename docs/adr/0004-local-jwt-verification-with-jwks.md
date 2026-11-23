# 0004. Verify access tokens locally against the Identity JWKS

- **Status**: Accepted
- **Date**: 2026-11-23

## Context

The read API (`GET /notifications`, `GET /notifications/{id}`, US-04) had no authentication, so
any caller could read every customer's notifications. `ecommerce-identity` issues RS256 access
tokens (`sub`, `iss`, `aud`, `iat`, `exp`, `roles`, and `customerId` for customers) and publishes
its public signing keys at a JWKS endpoint. The other services verify those tokens themselves.

Forces:

- Notifications is event-driven (constitution Principle II). Its startup, readiness and event
  processing must never depend on another service being reachable.
- Verification runs on every read request; a network call per request would add latency and make
  Identity a hard runtime dependency.
- Keys rotate: a new `kid` must be accepted without a restart, yet a flood of forged `kid` values
  must not turn into a flood of requests to Identity.
- The JWT pitfalls (`alg: none`, HS256 signed with the public key) must be closed by design.

Options considered:

1. Call an Identity introspection endpoint per request. Simple, but every read then depends on
   Identity being up, and Identity does not offer one.
2. Use `PyJWKClient` from PyJWT. Synchronous (urllib) inside an async service, and it refetches the
   key set for every unknown `kid` with no cooldown.
3. Verify locally with PyJWT against keys we fetch and cache ourselves with `httpx.AsyncClient`.

## Decision

We will verify access tokens locally (option 3):

- `PyJWT[crypto]` decodes with the fixed algorithm list `["RS256"]` and checks `iss`, `aud`, `exp`
  (with `JWT_CLOCK_TOLERANCE_SECONDS` of leeway) and the required claims. The header's `alg` and
  `kid` are checked before any key lookup, so a forged header never causes a fetch.
- `infrastructure/auth/jwks.py::JwksKeyStore` fetches `IDENTITY_JWKS_URL` lazily on the first
  authenticated request (never at startup), caches the keys by `kid`, and refetches only for an
  unknown `kid` and at most once per 60 s cooldown, behind one lock. A failed or malformed fetch
  keeps the cached keys.
- No keys obtainable at all → 503 `authentication unavailable`. Any token problem → one
  byte-identical 401 with `WWW-Authenticate: Bearer`. A valid token without a usable role → 403.
- Who sees what is a domain rule (`domain/access.py::AccessScope`): ADMIN sees everything,
  CUSTOMER sees only their own `customerId`. The scope is applied in the query itself.
- Every route declares its access: `PUBLIC` or `require_roles(...)`. `assert_route_policies` makes
  the application fail to build when a route declares neither (default deny).

## Consequences

- Identity being down never affects startup, readiness or Kafka consumption; once keys are cached,
  reads keep working through an Identity outage.
- A rotated key is picked up by one refresh, without a restart. A key revoked by Identity stays
  usable here until the process restarts or a refresh for another unknown `kid` replaces the set;
  tokens are short-lived, so this is accepted.
- `httpx` and `pyjwt[crypto]` become runtime dependencies.
- Four new settings (`IDENTITY_JWKS_URL`, `JWT_ISSUER`, `JWT_AUDIENCE`,
  `JWT_CLOCK_TOLERANCE_SECONDS`), documented in the README and set in the Dockerfile.
- The tests need no Identity service: `tests/support/tokens.py` signs tokens with a local RSA key
  and serves its JWKS over real HTTP, counting requests to prove the cache and the cooldown.
- The constitution (1.2.0, Principle II) now requires authentication and an explicit access
  policy on every route except health and the API documentation.
