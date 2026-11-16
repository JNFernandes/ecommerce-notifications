# CLAUDE.md

Notifications microservice: Python 3.12, FastAPI, aiokafka, MongoDB, DDD, event-driven with a
read-only HTTP API. The source of truth for every rule below is `.specify/memory/constitution.md`.
If this file and the constitution disagree, the constitution wins.

## Stack

- Python 3.12 **through uv only**: always `uv run ...`. The machine's global Python is 3.11 and
  must not be used. Add dependencies with `uv add` / `uv add --dev`, and commit `uv.lock`.
- FastAPI + uvicorn, Pydantic v2 + pydantic-settings, aiokafka, PyMongo async API
  (`AsyncMongoClient`)
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
  manager → `HealthService` on `app.state`).

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
  `ORDER_CANCELLED` (include `reason` when present), `PAYMENT_REFUNDED`. Channel `email`.
- Notifications are immutable. Messages come from domain templates: deterministic, ids and amounts
  only, no personal data beyond `customerId`.
- `payments.payment-refunded` has no `customerId`: resolve it through `OrderContext` by `orderId`
  (never `aggregateId`, which is the payment id). Missing context = retryable failure → retries →
  dead letter. Never skip, never store a notification without a customer (ADR 0003).
- Timestamps: UTC, timezone-aware (`datetime.now(UTC)`), ISO 8601. The Mongo client uses
  `tz_aware=True`.

## Kafka

- Consumed: `orders.order-placed`, `orders.order-confirmed`, `orders.order-cancelled`,
  `payments.payment-refunded`. **Produce nothing.**
- Manual offset commit after the save (`enable_auto_commit=False`). Exponential-backoff retries;
  when exhausted → `inbound_dead_letters` (payload, topic, partition, offset, error, attempts,
  timestamps) + log + commit the offset. Invalid or unparseable messages go straight to the dead
  letters.
- Inbound models ignore unknown fields and reject missing required ones; every field has a
  docstring and matches `../ecommerce-infra/docs/events.md`. A contract change updates that file in
  the same change set.
- `messaging/consumer_manager.py` (`KafkaConsumerManager`): runs in a background task, never
  blocks startup, reconnects with backoff, exposes `state` for `/health/ready`. Kafka down never
  makes the service unready. It subscribes to the topics of its `MessageHandler`s, processes
  **one message at a time**, and commits `offset + 1` only when the processor returns `HANDLED`
  (`STOPPED` = no commit). The connection attempt races the stop event, so shutdown is prompt.
- `messaging/processor.py` (`RetryingMessageProcessor`): `InvalidMessageError` → dead letter at
  once; any other error → retries with `RetryPolicy` backoff → dead letter. If the dead-letter
  store fails, it waits `max_delay` and processes the **whole message again**; it never returns
  `HANDLED` for an unhandled message (commits are cumulative per partition).
- Parse payloads with `decode_json_object` (`json.loads(..., parse_float=Decimal)`) and validate
  in Python mode with JSON-type guards (`BeforeValidator`s, see `messaging/order_placed.py`).
  Never `model_validate_json` for amounts: it loses precision on large values, and lax mode
  accepts `true` / `1.0` as `version: 1`.
- aiokafka is untyped: it is the one `ignore_missing_imports` override in `pyproject.toml`. Keep
  aiokafka types at the messaging boundary.

## HTTP

- Only `GET /health/live`, `GET /health/ready`, and (planned) `GET /notifications` (filters
  `customerId`, `orderId`, `type`; `limit` / `offset`; newest first) and `GET /notifications/{id}`.
- No endpoint may create, change or delete data. A write endpoint or a produced event needs an ADR
  and a constitution amendment first.
- Every endpoint and response model has descriptions (complete OpenAPI). CORS origins come from
  `CORS_ALLOWED_ORIGINS`, empty by default.

## MongoDB

- Collections: `notifications`, `order_context`, `inbound_dead_letters`.
- Indexes are declared **only** in `infrastructure/indexes.py` (`INDEXES`), applied at startup by
  `ensure_indexes`. Bump `INDEX_SCHEMA_VERSION` when they change (now 2). No ad-hoc scripts.
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
  consumer group per test), `tests/support/kafka.py` (topics, `produce` → partition/offset,
  `committed_offset`, `pause_kafka`). The topic is shared across component tests and consumed
  from the earliest offset, so assert only on this test's events and offsets.
- Names describe behaviour: `test_should_..._when_...`.
- Coverage: `--cov-fail-under=80` on `src/notifications/domain` and `src/notifications/application`.

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
  0003 consume `orders.order-placed` for the refund customer.
- Keep `README.md`, this file and the `Dockerfile` up to date with every behaviour change.

## Where things are (US-01)

`orders.order-placed` → `messaging/order_placed.py` (`OrderPlacedHandler`, `OrderPlacedEvent`) →
`application/record_order_received.py` (`RecordOrderReceivedService`: order context first, then
the notification, then `NotificationSender.send`) → `domain/` (`Notification.order_received`,
`OrderContext`, `messages.py` template, `time.py`) → `infrastructure/repositories/`
(`notifications.py`, `order_contexts.py`, `dead_letters.py`) and `infrastructure/sender.py`
(simulated send on the `notifications.send` logger). Retry settings: `CONSUMER_RETRY_*` in
`infrastructure/settings.py`, turned into a `RetryPolicy` in `main.py`. Spec and design:
`specs/001-order-received-notification/`.

## Active feature

US-02 order confirmed / cancelled: `specs/002-order-confirmed-cancelled/` (plan, research R1–R8).
Key decisions while implementing:
- One consumer with a routing table (`messaging/routing.py`): `Route(topic, parse, handle)`;
  `handle` is exactly one service method; a topic without a route is dead-lettered.
- Order event envelope and JSON-type guards are shared in `messaging/events.py`.
- Reason normalisation and templates are domain functions (`domain/messages.py`).
- Topic names are settings (`KAFKA_TOPIC_ORDER_*`), defaults = contract names.
