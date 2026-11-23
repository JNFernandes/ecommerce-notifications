# Research: JWT authentication and scoped read API (US-05)

Each entry gives the decision, then the reason. Alternatives appear only where they mattered.

**R1. Token library.** `PyJWT[crypto]` (`jwt.decode(..., algorithms=["RS256"])`, `PyJWKSet.from_dict`).
It is the standard Python JWT library, and a fixed algorithm list rejects `none` and the HS256
public-key confusion. *Rejected:* `python-jose`, which is barely maintained, and `authlib`, which is far larger than we need.

**R2. JWKS fetch.** We write our own `JwksKeyStore` on top of `httpx.AsyncClient` (3 s timeout). `httpx` moves from a dev
to a runtime dependency. It is async, so the event loop never blocks, and it is already used in the tests. *Rejected:*
`PyJWKClient`, which is synchronous (urllib) and refetches on every unknown `kid` with no cooldown.

**R3. Cache and cooldown.** The store holds keys in memory, keyed by `kid`. Keys are fetched on first need. A refetch happens only
for an unknown `kid`, and only if 60 s have passed since the last attempt, successful or not. One `asyncio.Lock` with a
re-check after acquiring it means a burst of requests causes a single fetch (SC-004). The clock is injected (`time.monotonic`).

**R4. Fetch failure.** If keys are cached, the store keeps them, and an unknown `kid` gets 401. If nothing is cached, the store raises
`AccessTokenUnavailableError`, which gives 503 (FR-007). A malformed JWKS counts as a failed fetch.

**R5. Errors.** Every rejection of a token raises one typed `InvalidAccessTokenError`, which carries no cause in the response and never the
token. The only other error is `AccessTokenUnavailableError`, for the 503 case. Both live in `application/errors.py`. The cause is
logged at DEBUG without the token. A single error type makes the 401 responses byte-identical (SC-002).

**R6. Verification order.** The verifier checks things in this order:
1. Parse the unverified header. `alg` must be `RS256` and `kid` must be present.
2. Look up the key by `kid`.
3. Call `jwt.decode` with `issuer`, `audience`, `leeway=JWT_CLOCK_TOLERANCE_SECONDS`, and `require=["exp","iat","sub","iss","aud"]`.

Checking the algorithm before fetching means a forged header never triggers a fetch.

**R7. Principal.** `Principal` is a frozen Pydantic model in `application/auth.py`:
- `subject: str`
- `customer_id: UUID | None`
- `roles: frozenset[Role]`

Unknown roles are dropped. If `roles` is not a list, the role set is empty. A `customerId` that is not a valid UUID makes the token invalid (401).

**R8. Scope as a domain rule.** `domain/access.py` holds the `Role` StrEnum, the `AccessScope` value object and
`AccessDeniedError`. `AccessScope.for_caller(roles, customer_id)` returns ADMIN → all, then CUSTOMER with a customerId → that
customer, and raises otherwise. Who sees what is a business rule, so it belongs in the domain (constitution I).

**R9. Conflicting customer filter.** `NotificationQueryService.list` returns an empty page and does not query when
`scope.permits_customer(filters.customer_id)` is false. The repository always adds `customerId = scope` to the filter for
list, count and by-id. The rule stays in the domain and the restriction stays in the query, as defence in depth.
Mongo cannot hold two conditions on one key without `$and`, which is why the conflict is handled before the query.

**R10. Indexes.** No change; the index schema stays at v3. A customer scope plus an `orderId` or `type` filter is still served by an index on
`(field, createdAt -1, _id -1)` with no sort in memory. `explain()` checks this in the integration tests.

**R11. FastAPI wiring.**
- `api/auth.py` defines `authenticate`, which uses `HTTPBearer(auto_error=False)`. A missing or non-Bearer header raises `InvalidAccessTokenError`.
- `require_roles(*roles)` returns a `RoleRequirement` callable that is used as a dependency.
- `PUBLIC` is a marker dependency.

Exception handlers in `api/errors.py` map 401 (with `WWW-Authenticate: Bearer`), 403 and 503.

**R12. Route policy check.** `api/route_policy.py::assert_route_policies(app)` runs in `build_api` after the routers are
included. It walks each `APIRoute`'s dependant tree and raises `RoutePolicyError` if a route has neither `PUBLIC` nor a
`RoleRequirement`. FastAPI's own docs routes are not `APIRoute`s and are public by design (FR-009).

**R13. OpenAPI.**
- A `bearerAuth` HTTP bearer scheme comes from `HTTPBearer`.
- `openapi_extra` adds `x-public: true` or `x-roles: [...]` to each route, the same convention ecommerce-identity uses.
- Protected routes document their 401, 403 and 503 responses.

**R14. CORS.** `allow_headers=["Authorization"]` (FR-017).

**R15. Test tokens.** `tests/support/tokens.py` provides:
- `TokenIssuer`, which makes an RSA-2048 key with `cryptography` and has `sign(**claims)`, with header overrides for `alg`, `kid` and `none`.
- `JwksServer`, a stdlib `ThreadingHTTPServer` on `127.0.0.1:0` that counts its requests.

The tests run a real local endpoint with no Identity service, and the request count proves the cooldown. A key-outage test points at `127.0.0.1:1`.

**R16. Composition.**
- `main.py` builds the `JwksKeyStore` and `JwtAccessTokenVerifier` from settings and stores them on `app.state.access_token_verifier`.
- Nothing is fetched at startup (FR-002/003).
- The `httpx` client is closed in the lifespan.

**R17. Docs.**
- ADR 0004 records local JWKS verification.
- The constitution goes to 1.2.0 (MINOR): Principle II gains authentication and route policy.
- README and CLAUDE.md drop "No authentication" and gain the four settings.
