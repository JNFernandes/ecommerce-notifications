# Implementation Plan: JWT authentication and scoped notifications API (US-05)

**Branch**: `feature/US-05-jwt-auth-scoped-api` | **Date**: 2026-11-23 | **Spec**: [spec.md](spec.md)

**Input**: `specs/005-jwt-auth-scoped-api/spec.md` and the planning input (verifier port with a JWKS cache, FastAPI auth
dependencies, a route-policy check at startup, a scope value object applied in the Mongo filter, four settings, a test token
helper).

## Summary

The service verifies ecommerce-identity RS256 access tokens locally, with keys from a lazy, cached JWKS that refetches at most
once a minute and never blocks the event loop (`httpx.AsyncClient`).

The token becomes a `Principal`, and the principal becomes a domain `AccessScope`: all notifications for ADMIN, one customer's for CUSTOMER.
`NotificationQueryService` passes the scope to the repository, which adds it to the MongoDB filter for list, count and by-id.

Health and docs stay public. Every other route must declare its roles, and a route that declares neither public nor roles stops the
service at startup. Kafka consumption does not change.

## Technical Context

**Language/Version**: Python 3.12 (uv)

**Primary Dependencies**: FastAPI, Pydantic v2, PyMongo async. New: `pyjwt[crypto]`. `httpx` moves from dev to runtime (research R1, R2).

**Storage**: MongoDB `notifications`, read only. No index change (v3, research R10).

**Testing**: pytest, pytest-asyncio, httpx `ASGITransport`, Testcontainers. Tokens come from the local `TokenIssuer` and `JwksServer` (R15).

**Target Platform**: Linux container; local development on Windows with uv.

**Project Type**: Event-driven web service with a read-only HTTP API.

**Performance Goals**: No call to Identity per request. At most one JWKS fetch per minute (SC-004).

**Constraints**:
- The service starts, and stays ready, with Identity down.
- The token is never logged.
- Every 401 is byte-identical.

**Scale/Scope**: 2 protected routes, 2 public routes, 2 roles.

No `NEEDS CLARIFICATION` remains (research R1–R17).

## Constitution Check

| # | Principle | Compliance | Status |
|---|---|---|---|
| I | Layers | The domain holds `Role` and `AccessScope` with no framework imports. The application holds the `Principal`, the verifier port, typed errors and scope orchestration. The JWT and JWKS adapters live in infrastructure. The API holds only the dependencies and the error mapping. Routers still make one service call each. | ✅ |
| II | Read-only API | No new endpoint and no writes. Adding authentication materially extends this principle, so the constitution moves to **1.2.0 (MINOR)** in this change set (spec Assumptions; R17). | ✅ (amendment) |
| III / IV / VIII | Write flow, integrity, Kafka | Untouched. A component test shows consumers work without any token. | ✅ |
| V | Types | Frozen Pydantic `Principal`; `Role` is a `StrEnum`; typed errors (`InvalidAccessTokenError`, `AccessTokenUnavailableError`, `AccessDeniedError`); mypy strict. PyJWT ships typed. | ✅ |
| VI / VII | Tests | Unit tests cover the verifier, the key store, the scope, the service and the route policy. Integration tests cover the scoped queries on real Mongo. Component tests cover the full auth flow, and every existing component HTTP read sends an admin token. | ✅ |
| IX / X | Branch / gate | `feature/US-05-...`, with the four-command gate on every task. | ✅ |
| Stack | New runtime dependencies | PyJWT adds a capability the stack lacks. `httpx` does not duplicate anything. ADR 0004 records local verification. | ✅ |

**Post-design re-check**: everything passes. Two risks carry over to the tasks:
- Every existing API and component test must send a token. The `assert_*_readable_over_http` helpers use an admin token.
- The route-policy check must ignore FastAPI's own docs routes.

## Project Structure

```text
specs/005-jwt-auth-scoped-api/  plan.md research.md data-model.md quickstart.md contracts/http-auth.md

src/notifications/
├── domain/access.py                      # NEW Role, AccessScope, AccessDeniedError
├── application/auth.py                   # NEW Principal
├── application/{ports,errors}.py         # + AccessTokenVerifier; token errors; scoped NotificationReader
├── application/query_notifications.py    # list/get take an AccessScope
├── infrastructure/auth/{__init__,jwks,verifier}.py   # NEW JwksKeyStore, JwtAccessTokenVerifier
├── infrastructure/settings.py            # + 4 settings
├── infrastructure/repositories/notifications.py      # scope in _query / find_by_id
├── api/auth.py                           # NEW authenticate, require_roles, PUBLIC, ScopeDependency
├── api/route_policy.py                   # NEW assert_route_policies
├── api/{app,errors,health,notifications}.py          # policy check, 401/403/503, markers, CORS header
└── main.py                               # build verifier, put it on app.state, close httpx

tests/
├── support/tokens.py                     # NEW TokenIssuer, JwksServer
├── support/{app,fakes}.py                # tokens in run_app / readable helpers; scoped fake reader
├── unit/domain/test_access.py            # NEW
├── unit/infrastructure/test_{jwt_verifier,jwks_key_store}.py  # NEW
├── unit/api/test_{route_policy,auth}.py  # NEW; endpoints tests updated to use tokens
├── unit/application/test_query_notifications.py      # scope cases
├── integration/test_notification_queries.py          # scoped list/count/by-id + explain
└── component/test_notifications_auth_flow.py         # NEW

docs/adr/0004-local-jwt-verification-with-jwks.md     # NEW
.specify/memory/constitution.md (1.2.0), README.md, CLAUDE.md, Dockerfile (env)
```

**Structure Decision**: The existing single-service layout is kept. New modules sit in the layer that owns them.

## Complexity Tracking

None.
