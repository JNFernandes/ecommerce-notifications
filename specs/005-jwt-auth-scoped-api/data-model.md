# Data model: JWT authentication and scoped read API (US-05)

Nothing new is persisted. The collections and the index schema (v3) stay the same.

## Domain (`domain/access.py`, plain Python)

| Type | Shape | Rules |
|---|---|---|
| `Role` | `StrEnum`: `CUSTOMER`, `ADMIN` | Roles not listed here are ignored. |
| `AccessScope` | frozen dataclass `customer_id: UUID \| None`. `None` means all customers. Built with `AccessScope.all()` or `AccessScope.customer(id)`. | `for_caller(roles, customer_id)`: ADMIN gives all (ADMIN wins over CUSTOMER); otherwise CUSTOMER with a customerId gives that customer; otherwise it raises `AccessDeniedError`. `permits_customer(requested)` is true when `requested` is None, the scope is all customers, or `requested` equals the scoped customer. |
| `AccessDeniedError` | domain error | Becomes 403 in `api/errors.py`. |

## Application

| Type | Where | Shape / contract |
|---|---|---|
| `Principal` | `application/auth.py` | Frozen Pydantic model: `subject: str`, `customer_id: UUID \| None`, `roles: frozenset[Role]`. `scope()` returns `AccessScope.for_caller(self.roles, self.customer_id)`. It is never stored or logged. |
| `AccessTokenVerifier` | `application/ports.py` (Protocol) | `async verify(token: str) -> Principal` |
| `InvalidAccessTokenError` | `application/errors.py` | Becomes 401. Its message is generic and never includes the token. |
| `AccessTokenUnavailableError` | `application/errors.py` | Raised when no keys are cached and none can be fetched. Becomes 503. |
| `NotificationReader` | `application/ports.py` | Every method gains a `scope: AccessScope`: `find_page(scope, filters, limit, offset)`, `count(scope, filters)`, `find_by_id(scope, id)` |
| `NotificationQueryService` | `application/query_notifications.py` | `list(scope, filters, limit, offset)` returns an empty page (`total` 0) without querying when `not scope.permits_customer(filters.customer_id)`. `get(scope, id)`: when the notification is missing or out of scope, it raises `NotificationNotFoundError` (the same 404). |

## Infrastructure

| Type | Where | Contract |
|---|---|---|
| `JwksKeyStore` | `infrastructure/auth/jwks.py` | `async get(kid) -> PyJWK`. Caching, cooldown and single-flight follow research R3 and R4. It is built with `url`, an `httpx.AsyncClient`, `cooldown=60.0` and `clock=time.monotonic`. |
| `JwtAccessTokenVerifier` | `infrastructure/auth/verifier.py` | Implements `AccessTokenVerifier` with the steps in research R6 and maps every PyJWT, header or claim error to `InvalidAccessTokenError`. |
| `MongoNotificationRepository._query(scope, filters)` | `infrastructure/repositories/notifications.py` | A scoped customer adds `customerId` to the filter. `find_by_id` uses `{_id, customerId?}`. |
| `Settings` | `infrastructure/settings.py` | The new settings are listed below. |

| Setting | Default | Rule |
|---|---|---|
| `IDENTITY_JWKS_URL` | `http://localhost:3001/.well-known/jwks.json` | http(s) URL |
| `JWT_ISSUER` | `http://localhost:3001` | non-empty |
| `JWT_AUDIENCE` | `ecommerce` | non-empty |
| `JWT_CLOCK_TOLERANCE_SECONDS` | `5` | `0 ≤ n ≤ 300` |

## API (`api/auth.py`, `api/route_policy.py`)

- `authenticate(request, credentials) -> Principal`: reads the verifier from `app.state` and raises `InvalidAccessTokenError` when the header is missing or is not Bearer.
- `require_roles(*roles: Role) -> RoleRequirement`: a dependency that returns the `Principal` and raises `AccessDeniedError` when the caller has none of the roles. A router derives `scope` from it through a `ScopeDependency` (`principal.scope()`).
- `PUBLIC`: a marker dependency on the health routers.
- `assert_route_policies(app)`: raises `RoutePolicyError` at startup (see research R12).
