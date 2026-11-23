# Tasks: JWT authentication and scoped notifications API (US-05)

**Input**: `specs/005-jwt-auth-scoped-api/` (plan, spec, research R1–R17, data-model, contracts/http-auth.md, quickstart)

**Shape**: few, coarse tasks. Each task writes its code **and** its tests, then runs the four-command gate
(`uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest --cov`) and is committed with a
Conventional Commit scoped `US-05`. Never leave the gate red between tasks.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 customer scope, US2 token refusal, US3 admin view, US4 availability and self-description

---

## Phase 1: Setup

- [X] T001 Add runtime deps with `uv add "pyjwt[crypto]" httpx` (move `httpx` out of the dev group in `pyproject.toml`), commit `uv.lock`; add the four settings `IDENTITY_JWKS_URL` (http(s) URL, default `http://localhost:3001/.well-known/jwks.json`), `JWT_ISSUER` (non-empty, default `http://localhost:3001`), `JWT_AUDIENCE` (non-empty, default `ecommerce`), `JWT_CLOCK_TOLERANCE_SECONDS` (int 0–300, default 5) to `src/notifications/infrastructure/settings.py` with docstrings, plus a settings unit test for the defaults and the bounds in `tests/unit/infrastructure/test_settings.py`

---

## Phase 2: Foundational (blocks every story)

- [X] T002 [P] Create `src/notifications/domain/access.py`: `Role` StrEnum (`CUSTOMER`, `ADMIN`), frozen dataclass `AccessScope(customer_id: UUID | None)` with `all()`, `customer(id)`, `for_caller(roles, customer_id)` (ADMIN → all, wins over CUSTOMER; CUSTOMER + customerId → that customer; else raise) and `permits_customer(requested)`, and `AccessDeniedError`; no framework imports. Unit tests (happy path + edge per method, incl. both roles, CUSTOMER without customerId, customerId without role) in `tests/unit/domain/test_access.py`
- [X] T003 [P] Create `tests/support/tokens.py`: `TokenIssuer` (RSA-2048 via `cryptography`, `kid`, `public_jwk()`, `sign(**claims)` with sensible defaults for `sub`/`iss`/`aud`/`iat`/`exp`/`roles`, header overrides for `alg`/`kid`, an unsigned `alg: none` token, and an HS256 token signed with the PEM public key) and `JwksServer` (stdlib `ThreadingHTTPServer` on `127.0.0.1:0` in a daemon thread, serves a mutable key list or a malformed body, counts requests, context manager)
- [X] T004 Add `Principal` (frozen Pydantic: `subject`, `customer_id: UUID | None`, `roles: frozenset[Role]`, `scope()` → `AccessScope.for_caller`) in `src/notifications/application/auth.py`; `AccessTokenVerifier` Protocol (`async verify(token) -> Principal`) in `src/notifications/application/ports.py`; `InvalidAccessTokenError` (generic message, never the token) and `AccessTokenUnavailableError` in `src/notifications/application/errors.py`
- [X] T005 Create `src/notifications/infrastructure/auth/__init__.py` (module docstring), `jwks.py` (`JwksKeyStore(url, client: httpx.AsyncClient, cooldown=60.0, clock=time.monotonic)`, `async get(kid) -> PyJWK`: lazy fetch, in-memory cache by `kid`, refetch only for unknown `kid` and only if `cooldown` passed since the last attempt, one `asyncio.Lock` with re-check after acquire, malformed JWKS = failed fetch, nothing cached + failure → `AccessTokenUnavailableError`, cached + unknown `kid` → `InvalidAccessTokenError`) and `verifier.py` (`JwtAccessTokenVerifier`: research R6 order — unverified header `alg == RS256` and `kid` present before any fetch, key by `kid`, `jwt.decode(algorithms=["RS256"], issuer, audience, leeway, require=["exp","iat","sub","iss","aud"])`; unknown roles dropped, non-list `roles` → empty, bad `customerId` UUID → invalid; every failure → `InvalidAccessTokenError`, cause logged at DEBUG without the token). Tests with `TokenIssuer`/`JwksServer` in `tests/unit/infrastructure/test_jwks_key_store.py` (lazy, cache hit, cooldown, burst of concurrent unknown `kid`s → one request, outage `127.0.0.1:1` → 503 error, malformed set, cached keys survive outage, rotation) and `tests/unit/infrastructure/test_jwt_verifier.py` (valid, expired, within tolerance, wrong signature, `none`, HS256 with public key, wrong iss/aud, missing/unknown `kid`, roles/customerId edge cases)
- [X] T006 Create `src/notifications/api/auth.py` (`authenticate` via `HTTPBearer(auto_error=False, scheme_name="bearerAuth")` reading `app.state.access_token_verifier`, missing/non-Bearer/empty → `InvalidAccessTokenError`; `RoleRequirement` + `require_roles(*roles)`, `AccessDeniedError` when no role matches; `PUBLIC` marker dependency; `ScopeDependency` = `principal.scope()`) and `src/notifications/api/route_policy.py` (`RoutePolicyError`, `assert_route_policies(app)` walking each `APIRoute`'s dependant tree; non-`APIRoute` docs routes ignored). In `src/notifications/api/errors.py` map `InvalidAccessTokenError` → 401 `{"detail": "invalid or missing access token"}` + `WWW-Authenticate: Bearer`, `AccessDeniedError` → 403 `{"detail": "access denied"}`, `AccessTokenUnavailableError` → 503 `{"detail": "authentication unavailable"}`. Tests in `tests/unit/api/test_auth.py` and `tests/unit/api/test_route_policy.py` (undeclared route raises; health + docs pass)

**Checkpoint**: verifier, scope and auth dependencies exist and are unit-tested; nothing is wired into routes yet.

---

## Phase 3: US1 + US2 — customer scope and token refusal (P1) 🎯 MVP

**Goal**: every `/notifications` call needs a valid token; a customer sees only their own data.
**Independent test**: two customers' notifications; each token sees only its own (list, `total`, filters, by-id 404); every bad-token case gets the byte-identical 401.

- [X] T007 [US1] Scope the read path: `NotificationReader` methods take `scope: AccessScope` in `src/notifications/application/ports.py`; `NotificationQueryService.list(scope, filters, limit, offset)` returns an empty page (`total` 0) without querying when `not scope.permits_customer(filters.customer_id)`, `get(scope, id)` raises `NotificationNotFoundError` when missing or out of scope, in `src/notifications/application/query_notifications.py`; `MongoNotificationRepository._query(scope, filters)` adds `customerId` for a scoped customer and `find_by_id` filters `{_id, customerId?}` in `src/notifications/infrastructure/repositories/notifications.py`; update the fake reader in `tests/support/fakes.py`. Tests: scope cases in `tests/unit/application/test_query_notifications.py`; scoped list/count/by-id + `explain()` showing no in-memory sort for customer scope with `orderId`/`type` in `tests/integration/test_notification_queries.py`
- [X] T008 [US1] [US2] Wire auth into the API: routes in `src/notifications/api/notifications.py` depend on `require_roles(Role.CUSTOMER, Role.ADMIN)` + `ScopeDependency`, pass the scope to the service, add `openapi_extra={"x-roles": ["CUSTOMER","ADMIN"]}` and documented 401/403/503 responses; health routes in `src/notifications/api/health.py` get `PUBLIC` + `x-public: true`; `src/notifications/api/app.py` calls `assert_route_policies` after including routers and sets CORS `allow_headers=["Authorization"]`; `src/notifications/main.py` builds `httpx.AsyncClient(timeout=3)`, `JwksKeyStore`, `JwtAccessTokenVerifier` into `app.state.access_token_verifier` (no fetch at startup) and closes the client in the lifespan. Update `tests/unit/api/test_notifications_endpoints.py` to send tokens (fake verifier) and add 401 (no header, non-Bearer, invalid → identical bodies + `WWW-Authenticate`), 403, 503, OpenAPI `bearerAuth`/`x-public`/`x-roles`, CORS `Authorization` cases
- [X] T009 [US1] [US2] Component tests: `tests/support/app.py` — `component_settings` points `IDENTITY_JWKS_URL` at a `JwksServer` and `run_app` / `assert_readable_over_http` / `assert_stored_readable_over_http` send an admin token, so every existing component test keeps passing; new `tests/component/test_notifications_auth_flow.py` covering US1 scenarios 1–5 and US2 scenarios 1–4 (byte-identical 401s; `caplog` shows no token string in any log line or body)

**Checkpoint**: MVP — the leak is closed. Run the gate and commit.

---

## Phase 4: US3 — admin view (P2)

**Goal**: ADMIN sees and filters everything; a token with no usable role gets 403.

- [ ] T010 [US3] Add admin cases to `tests/component/test_notifications_auth_flow.py`: list without filters returns both customers with the right `total`, `customerId` = B filters to B, read A's notification by id, CUSTOMER+ADMIN gets the admin view, valid token with no ADMIN and no customerId → 403 (fix any gap found in T002/T007)

---

## Phase 5: US4 — availability and self-description (P3)

**Goal**: Identity's availability never affects startup, readiness or Kafka.

- [ ] T011 [US4] Add to `tests/component/test_notifications_auth_flow.py`: with `IDENTITY_JWKS_URL` = `http://127.0.0.1:1/...` the app starts, `/health/live` and `/health/ready` answer 200 without a token, an order event is still consumed and stored, a token request gets 503; with a reachable `JwksServer`, a cached key keeps working after the server stops, and a rotated `kid` is accepted after one refresh

---

## Phase 6: Polish & documentation

- [ ] T012 [P] Write `docs/adr/0004-local-jwt-verification-with-jwks.md` (template `docs/adr/template.md`) and add it to `docs/adr/README.md`; amend `.specify/memory/constitution.md` to 1.2.0 (Principle II: authentication, default deny, route policy; sync report at the top)
- [ ] T013 [P] Update `README.md` (auth, the four settings, access rules, 401/403/503, manual quickstart steps), `CLAUDE.md` (replace "No authentication", add US-05 to "Where things are", settings, `tests/support/tokens.py`, admin token in the readable helpers) and `Dockerfile` (env defaults for the four settings)
- [ ] T014 Final gate run, then walk `specs/005-jwt-auth-scoped-api/quickstart.md` manual steps against ecommerce-infra if Identity is available (note in the PR if skipped)

---

## Dependencies

- T001 → everything. T002 and T003 in parallel; T004 needs T002; T005 needs T003 + T004; T006 needs T004.
- T007 needs T002; T008 needs T005–T007; T009 needs T008.
- T010 and T011 need T009 and can run in parallel (same file, so do them back to back if one agent).
- T012 and T013 can start after T008; T014 last.

## Parallel examples

- After T001: T002 (`domain/access.py`) ‖ T003 (`tests/support/tokens.py`).
- After T009: T012 (ADR + constitution) ‖ T013 (README, CLAUDE.md, Dockerfile) ‖ T010/T011.

## Implementation strategy

1. **MVP** = T001–T009: authentication plus customer scope; the leak is closed and every existing test sends a token.
2. T010 (admin) and T011 (resilience) mostly add tests; the behaviour already exists from the foundation.
3. T012–T014 finish docs and governance in the same change set, as FR-019 requires.
