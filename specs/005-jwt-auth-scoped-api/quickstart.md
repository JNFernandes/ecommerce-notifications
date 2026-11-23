# Quickstart: validate US-05

## Automated (no Identity service needed)

Run the quality gate in order:

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest --cov
```

Docker must be running. The tests sign their own tokens with `tests/support/tokens.py` (`TokenIssuer`) and serve the keys from a local
`JwksServer`.

| Scenario | Test |
|---|---|
| Verifier cases: valid, expired, within tolerance, wrong signature, `alg: none`, HS256 with the public key, wrong issuer or audience, missing or unknown `kid`, keys unavailable (503), single refetch under a burst | `tests/unit/infrastructure/test_jwt_verifier.py`, `test_jwks_key_store.py` |
| Scope construction and `permits_customer` | `tests/unit/domain/test_access.py` |
| Query service passes the scope; a conflicting filter gives an empty page; out of scope gives 404 | `tests/unit/application/test_query_notifications.py` |
| Every route declares public or roles; an undeclared route stops startup | `tests/unit/api/test_route_policy.py` |
| 401 / 403 / 503 mapping, `WWW-Authenticate`, OpenAPI `bearerAuth` and `x-public` / `x-roles`, CORS `Authorization` | `tests/unit/api/test_notifications_endpoints.py`, `test_auth.py` |
| Scoped list, count and by-id on real MongoDB, with the index used (`explain`) | `tests/integration/test_notification_queries.py` |
| End-to-end: own notifications and totals, a foreign id gives 404, a foreign filter gives an empty page, admin sees all and filters, 403, 401, health is public, consumers run without a token, no token in the logs | `tests/component/test_notifications_auth_flow.py` |

## Manual, against ecommerce-infra and ecommerce-identity

1. Start the local infrastructure: `docker compose up -d` in `ecommerce-infra` (Identity on `:3001`).
2. Start the service:

   ```sh
   uv run uvicorn notifications.main:create_app --factory --port 8000
   ```

   It starts even when Identity is down.
3. Log in through Identity and copy `accessToken`. Then run these checks:

   | Request | Expected |
   |---|---|
   | `curl -i localhost:8000/notifications` | 401 with `WWW-Authenticate: Bearer` |
   | `curl -H "Authorization: Bearer $TOKEN" localhost:8000/notifications` | only your own notifications |
   | `curl localhost:8000/health/ready` | 200 without a token |
4. Stop Identity and restart the service, then send a request with a token. Expected: 503, while `/health/ready` stays 200.
