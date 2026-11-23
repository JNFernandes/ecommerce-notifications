# HTTP contract changes (US-05)

This file extends `specs/004-notifications-read-api/contracts/http-api.md`. The paths, parameters and response bodies
of 200, 404 and 422 do not change.

## Access per route

| Route | Access | OpenAPI extension |
|---|---|---|
| `GET /health/live`, `GET /health/ready` | public | `x-public: true` |
| `/docs`, `/redoc`, `/openapi.json` | public (FastAPI) | — |
| `GET /notifications` | `CUSTOMER` (own notifications only), `ADMIN` (all) | `x-roles: ["CUSTOMER", "ADMIN"]` |
| `GET /notifications/{id}` | `CUSTOMER` (own notifications only), `ADMIN` (all) | `x-roles: ["CUSTOMER", "ADMIN"]` |

Security scheme: `bearerAuth` (`type: http`, `scheme: bearer`, `bearerFormat: JWT`), applied to the protected routes.

## Request

`Authorization: Bearer <RS256 access token from ecommerce-identity>`

The verifier requires:
- header `alg` = `RS256` and a `kid`;
- `iss` = `JWT_ISSUER` and `aud` = `JWT_AUDIENCE`;
- `exp` and `iat`, with `exp` checked within ± `JWT_CLOCK_TOLERANCE_SECONDS`;
- `sub`.

The claims `customerId` (UUID, optional) and `roles` (list) are read as well.

## Scoping

| Caller | `GET /notifications` | `GET /notifications/{id}` |
|---|---|---|
| ADMIN (with or without CUSTOMER) | all notifications; any filters | any notification |
| CUSTOMER with `customerId` = C | only C's notifications; `customerId` ≠ C → `{"items": [], "total": 0, ...}` | C's notification → 200; anything else → 404 (same body as an unknown id) |

## New error responses (body `{"detail": ...}`)

| Status | When | Body | Headers |
|---|---|---|---|
| 401 | no header, a scheme other than Bearer, or a token that is malformed, expired, wrongly signed, from the wrong issuer or audience, has no or an unknown `kid`, or uses an algorithm other than RS256 | `{"detail": "invalid or missing access token"}` (identical for every cause) | `WWW-Authenticate: Bearer` |
| 403 | valid token without ADMIN and without (CUSTOMER and `customerId`) | `{"detail": "access denied"}` | — |
| 503 | Identity keys are needed, none are cached and the fetch fails | `{"detail": "authentication unavailable"}` | — |

The token appears in no response body and in no log line.
