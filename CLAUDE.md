# CLAUDE.md

Notifications microservice: Python 3.12, FastAPI, aiokafka, MongoDB, DDD, event-driven with a
read-only HTTP API. The source of truth for every rule below is `.specify/memory/constitution.md`.
If this file and the constitution disagree, the constitution wins.

## Stack

- Python 3.12 **through uv only**: always `uv run ...`. The machine's global Python is 3.11 and
  must not be used. Add dependencies with `uv add` / `uv add --dev`, and commit `uv.lock`.
- FastAPI + uvicorn, Pydantic v2 + pydantic-settings, aiokafka, PyMongo async API
  (`AsyncMongoClient`), PyJWT[crypto] + httpx (access tokens and the Identity JWKS)
- pytest, pytest-asyncio (`asyncio_mode = "auto"`), pytest-cov, httpx, Testcontainers
  (`testcontainers.community.mongodb`, Kafka)
- Ruff (lint + format), mypy strict (with the pydantic plugin)
- Local infra comes from the `ecommerce-infra` repo (`docker compose up -d` there): MongoDB
  `localhost:27017` (`mongo_user` / `mongo_pass`, `authSource=admin`, database `notifications`),
  Kafka `localhost:9092` (`kafka:29092` inside the compose network `ecommerce_default`)
- HTTP port `8000`. Run locally: `uv run uvicorn notifications.main:create_app --factory --port 8000`

## Quality gate (after every task, before every commit)

Run these in order. All four must pass. Fix any failure before moving on.

```sh
uv run ruff check            # rules E F I B UP ANN S C4 SIM RUF; `--fix` for fixable findings
uv run ruff format --check   # `uv run ruff format` to fix
uv run mypy                  # strict, over src and tests; zero errors
uv run pytest --cov          # unit + component tests; 80% coverage of domain + application
```

Never commit a red gate. A task is not done until all four pass. `pytest` needs Docker running
(the component tests start MongoDB). No test may need Kafka to be running: tests that exercise a
broker outage point at `127.0.0.1:1`.

## Layering

**Messaging (Kafka consumers) and API (FastAPI routers) → Application service → Domain →
Repository.**

- Consumers (`messaging`) and routers (`api`): I/O only. Validate into a Pydantic model, call
  exactly one application service method. No logic.
- Application services (`application`): orchestrate. No business rules. Repository and probe
  interfaces are `typing.Protocol`s declared here.
- Domain (`domain`): **all** business logic, plain Python. Never import FastAPI, aiokafka, pymongo
  or Pydantic. Aggregates `Notification` and `OrderContext`; message templates live here.
- Infrastructure (`infrastructure`): settings, MongoDB client, persistence documents mapped
  explicitly to and from domain objects, the index module.
- `notifications/main.py` is the composition root (lifespan: MongoDB client → indexes → consumer
  manager → `HealthService`, the query service and the access-token verifier on `app.state`).

## Write flow (never deviate)

consume → validate (Pydantic) → one application service call → domain decides → MongoDB save →
**only then** commit the offset → log line (simulated send) only after the notification is stored.

MongoDB is standalone: no multi-document transactions. Every write is idempotent by itself
(ADR 0002):
- `Notification`: insert under a UNIQUE index on `sourceEventId`. Duplicate key on that index =
  already recorded = success, no second log line. Any other error is a real failure.
- `OrderContext`: `_id` = order id; `find_one_and_update` + `$setOnInsert` (first write wins,
  a conflicting later event only logs a WARNING), written before the `ORDER_RECEIVED`
  notification.
- Dead letter: insert under a UNIQUE index on `(topic, partition, offset)`; duplicate = already
  dead-lettered = success.

## Notification rules

- Types (`StrEnum`): `ORDER_RECEIVED` (`orders.order-placed`), `ORDER_CONFIRMED`,
  `ORDER_CANCELLED` (include `reason` when present), `PAYMENT_REFUNDED`,
  `ORDER_FAILURE_EXPLANATION` (`ai.order-triage-completed`). Channel `email`.
  Build them only through the `Notification` factories (`order_received`, `order_confirmed`,
  `order_cancelled`, `payment_refunded`, `order_failure_explanation`), which share `_create`
  (email, `SENT`, UTC millis).
- Cancellation reason: `domain/messages.py::normalise_reason` (runs of line breaks/tabs → one
  space, trim, drop one trailing period, empty → no reason, > 500 chars → cut + `…`). The parser
  passes the raw reason on; only the domain decides how it is shown. A non-string `reason` is an
  invalid message (dead letter).
- Confirmations and cancellations take the customer from the event and never touch
  `OrderContext`.
- Refunds (`payments.*`): `aggregateId` is the **payment** id; the order is `orderId`, always.
  The customer comes only from `OrderContextRepository.get(order_id)`; missing →
  `application/errors.py::OrderContextNotFoundError` (retryable, never `InvalidMessageError`):
  the processor retries, then dead-letters. Refunds never write order contexts. Amount: two
  decimals + currency code (`require_currency_code`, three uppercase letters). Late contexts
  after the dead letter are an accepted limitation (README).
- Notifications are immutable. Messages come from domain templates: deterministic, ids and amounts
  only, no personal data beyond `customerId`. Sole exception (constitution 1.3.0, ADR 0005):
  `ORDER_FAILURE_EXPLANATION`, whose message is the AI explanation cleaned by
  `domain/messages.py::plain_text` (control/format chars removed, whitespace collapsed, trimmed,
  ≤ 500 chars with `…`; empty → `InvalidExplanationError`). Plain text, never HTML.
- Order failure explanations are advisory: `domain/triage.py::TriageDetails`
  (`SuggestedAction`, `TriageSource`, confidence 0–1, cleaned `promptVersion`) is stored as the
  `triage` sub-document and returned as nullable API fields. Nothing may act on
  `suggestedAction`. The customer comes from `OrderContext` exactly like a refund
  (`application/order_customer.py::customer_of_order`).
- `payments.payment-refunded` has no `customerId`: resolve it through `OrderContext` by `orderId`
  (never `aggregateId`, which is the payment id). Missing context = retryable failure → retries →
  dead letter. Never skip, never store a notification without a customer (ADR 0003).
- Timestamps: UTC, timezone-aware (`datetime.now(UTC)`), ISO 8601. The Mongo client uses
  `tz_aware=True`.

## Kafka

- Consumed: `orders.order-placed`, `orders.order-confirmed`, `orders.order-cancelled`,
  `payments.payment-refunded`, `ai.order-triage-completed` (`aggregateId` = order id = `orderId`).
  **Produce nothing.**
- Manual offset commit after the save (`enable_auto_commit=False`). Exponential-backoff retries;
  when exhausted → `inbound_dead_letters` (payload, topic, partition, offset, error, attempts,
  timestamps) + log + commit the offset. Invalid or unparseable messages go straight to the dead
  letters.
- Inbound models ignore unknown fields and reject missing required ones; every field has a
  docstring and matches `../ecommerce-infra/docs/events.md`. A contract change updates that file in
  the same change set.
- **One consumer, one routing table** (`messaging/routing.py`): `Route(topic, parse, handle)`
  connects a topic to its parser and exactly one application service method; `RoutingTable` is
  the single `MessageHandler` given to the processor, and a topic without a route raises
  `InvalidMessageError` (dead letter, never dropped). Register routes only in
  `main.py::build_routing_table`; never add a second consumer. Topic names are settings
  (`KAFKA_TOPIC_*`, defaults = contract names).
- `messaging/consumer_manager.py` (`KafkaConsumerManager`): runs in a background task, never
  blocks startup, reconnects with backoff, exposes `state` for `/health/ready`. Kafka down never
  makes the service unready. It subscribes to `routing.topics`, processes
  **one message at a time**, and commits `offset + 1` only when the processor returns `HANDLED`
  (`STOPPED` = no commit). The connection attempt races the stop event, so shutdown is prompt.
- `messaging/processor.py` (`RetryingMessageProcessor`): `InvalidMessageError` → dead letter at
  once; any other error → retries with `RetryPolicy` backoff → dead letter. If the dead-letter
  store fails, it waits `max_delay` and processes the **whole message again**; it never returns
  `HANDLED` for an unhandled message (commits are cumulative per partition).
- Parse payloads with `decode_json_object` (`json.loads(..., parse_float=Decimal)`) and validate
  in Python mode with JSON-type guards (`BeforeValidator`s in `messaging/events.py`). Every event
  model extends `EventEnvelope` (`orders.*` events through `OrderEventEnvelope`, which adds
  `customerId`) and is parsed with `parse_event`; a parser returns the
  application command, so it plugs straight into a `Route`.
  Never `model_validate_json` for amounts: it loses precision on large values, and lax mode
  accepts `true` / `1.0` as `version: 1`.
- aiokafka is untyped: it is the one `ignore_missing_imports` override in `pyproject.toml`. Keep
  aiokafka types at the messaging boundary.

## HTTP

- Only `GET /health/live`, `GET /health/ready`, `GET /notifications` (filters `customerId`,
  `orderId`, `type`; `limit` 1–100 / `offset` ≥ 0; newest first with `_id` tie-break; exact
  `total`) and `GET /notifications/{id}`.
- Authentication (US-05, ADR 0004, constitution II): `/notifications*` need an RS256 bearer token
  from ecommerce-identity, verified locally: `infrastructure/auth/jwks.py::JwksKeyStore` (lazy
  fetch of `IDENTITY_JWKS_URL`, cache by `kid`, refetch only for an unknown `kid` after a 60 s
  cooldown, one lock; `create_app(jwks_refresh_cooldown_seconds=...)` shortens it in tests) and
  `infrastructure/auth/verifier.py::JwtAccessTokenVerifier` (`alg == RS256` + `kid` before any
  fetch; `iss` `JWT_ISSUER`, `aud` `JWT_AUDIENCE`, leeway `JWT_CLOCK_TOLERANCE_SECONDS`). Never
  fetch at startup; Identity never affects readiness or consumption. Never log a token.
- Access is a domain rule: `domain/access.py` (`Role`, `AccessScope.for_caller`: ADMIN → all,
  wins over CUSTOMER; CUSTOMER + `customerId` → that customer; else `AccessDeniedError`). The
  scope reaches the query (`NotificationReader` methods take `scope`): a foreign `customerId`
  filter → empty page without a query, a foreign id → the same 404 as an unknown id.
- Every route declares `PUBLIC` or `require_roles(...)` (`api/auth.py`) plus `x-public` /
  `x-roles` in `openapi_extra`; `api/route_policy.py::assert_route_policies` fails `build_api`
  for an undeclared route (default deny).
- Read path: `api/notifications.py` (router, `NotificationResponse`, `NotificationPageResponse`)
  → one `application/query_notifications.py::NotificationQueryService` call → the
  `NotificationReader` port (`find_page`, `count`, `find_by_id`), implemented by
  `MongoNotificationRepository`. Response models are separate from domain and documents.
- Errors are mapped only in `api/errors.py`: `NotificationNotFoundError` → 404,
  `PyMongoError` → 503 (never an empty result), `InvalidAccessTokenError` → 401
  `invalid or missing access token` + `WWW-Authenticate: Bearer` (byte-identical for every
  cause), `AccessDeniedError` → 403 `access denied`, `AccessTokenUnavailableError` → 503
  `authentication unavailable`, all as the Pydantic `ErrorResponse` (`{"detail": ...}`, FastAPI's
  standard shape); validation is FastAPI's standard 422.
- No endpoint may create, change or delete data. A write endpoint or a produced event needs an ADR
  and a constitution amendment first.
- Every endpoint and response model has descriptions (complete OpenAPI). CORS origins come from
  `CORS_ALLOWED_ORIGINS`, empty by default; CORS allows the `Authorization` header.

## MongoDB

- Collections: `notifications`, `order_context`, `inbound_dead_letters`.
- Indexes are declared **only** in `infrastructure/indexes.py` (`INDEXES`), applied at startup by
  `ensure_indexes`. Bump `INDEX_SCHEMA_VERSION` when they change (now 3). No ad-hoc scripts.
  A replaced index goes into `OBSOLETE_INDEX_NAMES`, which `ensure_indexes` drops. Every read
  filter has a `(field, createdAt -1, _id -1)` index (plus `(createdAt -1, _id -1)`), so list
  queries never sort in memory; keep that true for any new filter (checked with `explain()`).
- Mapping (`infrastructure/repositories/*`): UUIDs as strings, amounts as `Decimal128`
  (`Decimal` in the domain), timestamps as BSON dates truncated to milliseconds in the domain.
- Duplicate-key errors are mapped narrowly: only the `keyPattern` of the expected unique index
  means "already stored"; any other duplicate re-raises.

## Code rules

- mypy strict, zero errors. No `Any` without a justification at the narrowest scope.
  `# type: ignore[code]` only with a justification comment.
- Pydantic v2 models for every inbound message and HTTP response, `frozen=True` where possible.
- `StrEnum` for type, channel and status; no bare string literals for them.
- Typed domain exceptions. No bare `except:`; no `except Exception` without re-raise or handling;
  never swallow errors.
- Docstrings on every public module, class and function; each package `__init__.py` has a module
  docstring describing its responsibility.

## Tests

- Unit: domain (pure pytest, no FastAPI, DB or Kafka) and application (fakes in
  `tests/support/fakes.py`). Every method: one happy path and one error/edge case.
- Integration: repositories and unique indexes against real MongoDB (Testcontainers). No mongomock.
- Component: full app against real containers; drive the real lifespan with
  `app.router.lifespan_context(app)` and an httpx `AsyncClient` over `ASGITransport` (not
  Starlette's `TestClient`). Simulate other services by producing their events.
- No `time.sleep` or bare `asyncio.sleep` in tests: use `tests/support/polling.py` (`wait_until`
  for sync conditions, `eventually` for async probes such as DB queries or readiness).
- Component helpers: `tests/support/app.py` (`run_app`, `component_settings`: fresh database and
  consumer group per test, `IDENTITY_JWKS_URL` = the session's `shared_jwks_server()` serving the
  `TOKENS` key; `bearer`, `admin_headers`), `tests/support/tokens.py` (`TokenIssuer`: RS256 tokens
  plus forged ones, `customer_token` / `admin_token`; `JwksServer`: local JWKS over real HTTP that
  counts requests; no test needs Identity), `tests/support/kafka.py` (topics, `produce` → partition/offset,
  `committed_offset`, `pause_kafka`). Fixtures call `create_consumed_topics` (every topic the
  consumer subscribes to, `CONSUMED_TOPICS`); a new route needs its topic there first, or the
  consumer group stalls. Topics are shared across component tests and consumed from the earliest
  offset, so assert only on this test's events and offsets; `component_settings` uses fast
  retries so leftovers from other tests stay cheap. For a refund, wait for the order context
  before producing it.
- Names describe behaviour: `test_should_..._when_...`.
- Coverage: `--cov-fail-under=80` on `src/notifications/domain` and `src/notifications/application`.
- **Component tests and the read API (constitution VII, v1.1.0):** the read API exists, so every
  component test that records a notification MUST also read it back over HTTP with
  `tests/support/app.py::assert_readable_over_http` (a document) or
  `assert_stored_readable_over_http` (a query). Both read with an admin token.

## Branches and commits

- Branches: `feature/US-XX-short-description`, `fix/US-XX-short-description`,
  `chore/short-description`. One branch per user story, created from `main`; never commit to
  `main` directly. Delete branches after merge.
- `/speckit.specify`: clean tree → `git checkout main && git pull` →
  `git checkout -b feature/US-XX-...` → only then write the spec. Never write spec files on `main`.
- Conventional Commits with scope: `feat(US-01): add order confirmed notification`,
  `test(US-01): ...`, `docs(US-01): ...`. Infra-only work: `chore:`, `build:`, `docs:`, `test:`.
- PRs reference the user story: "Implements US-01: ...".

## Documentation

- ADRs in `docs/adr/` (template `docs/adr/template.md`, index `docs/adr/README.md`, four-digit
  numbers, never renumbered). Current: 0001 MongoDB, 0002 idempotency without transactions,
  0003 consume `orders.order-placed` for the refund customer, 0004 local JWT verification with
  JWKS, 0005 AI order failure explanations as untrusted, advisory text.
- Keep `README.md`, this file and the `Dockerfile` up to date with every behaviour change.

## Where things are

US-01: `orders.order-placed` → `messaging/order_placed.py` (`OrderPlacedEvent`, `parse_order_placed`) →
`application/record_order_received.py` (`RecordOrderReceivedService`: order context first, then
the notification, then `NotificationSender.send`) → `domain/` (`Notification.order_received`,
`OrderContext`, `messages.py` template, `time.py`) → `infrastructure/repositories/`
(`notifications.py`, `order_contexts.py`, `dead_letters.py`) and `infrastructure/sender.py`
(simulated send on the `notifications.send` logger). Retry settings: `CONSUMER_RETRY_*` in
`infrastructure/settings.py`, turned into a `RetryPolicy` in `main.py`. Spec and design:
`specs/001-order-received-notification/`.

US-02: `orders.order-confirmed` / `orders.order-cancelled` → `messaging/order_status.py`
(`parse_order_confirmed`, `parse_order_cancelled`) → `application/record_order_status.py`
(`RecordOrderStatusService.record_confirmed` / `record_cancelled`) → domain factories → the shared
`application/recording.py::record_and_send` (store, then send; duplicates log
`Event already recorded: type=… sourceEvent=… order=…`). Spec and design:
`specs/002-order-confirmed-cancelled/`.

US-03: `payments.payment-refunded` → `messaging/payment_refunded.py` (`parse_payment_refunded`,
order = `orderId`) → `application/record_payment_refunded.py` (`RecordPaymentRefundedService`:
`OrderContextRepository.get` → `OrderContextNotFoundError` or `Notification.payment_refunded` →
`record_and_send`). Spec and design: `specs/003-payment-refunded-notification/`.

US-04: `GET /notifications[/{id}]` → `api/notifications.py` → `NotificationQueryService`
(`application/query_notifications.py`) → `MongoNotificationRepository.find_page` / `count` /
`find_by_id`; handlers in `api/errors.py`; indexes v3. Spec and design:
`specs/004-notifications-read-api/`.

US-05: bearer auth on `/notifications*` → `api/auth.py` (`authenticate`, `require_roles`,
`PUBLIC`, `ScopeDependency`) and `api/route_policy.py` → `application/auth.py::Principal`
(`scope()`) and the `AccessTokenVerifier` port → `infrastructure/auth/` (`JwksKeyStore`,
`JwtAccessTokenVerifier`, built in `main.py` onto `app.state.access_token_verifier`) →
`domain/access.py` (`Role`, `AccessScope`). Settings `IDENTITY_JWKS_URL`, `JWT_ISSUER`,
`JWT_AUDIENCE`, `JWT_CLOCK_TOLERANCE_SECONDS`. Spec and design: `specs/005-jwt-auth-scoped-api/`.

AI explanations: `ai.order-triage-completed` → `messaging/order_triage_completed.py`
(`parse_order_triage_completed`: strict contract, `orderId == aggregateId`) →
`application/record_order_failure_explanation.py` (`RecordOrderFailureExplanationService`:
`customer_of_order` → `OrderContextNotFoundError` or `Notification.order_failure_explanation` →
`record_and_send`) → `domain/triage.py`, `domain/messages.py::plain_text`. Setting
`KAFKA_TOPIC_ORDER_TRIAGE_COMPLETED`. Component helper `tests/support/app.py::place_order`
(placement, then wait for its order context). Decision: ADR 0005.
