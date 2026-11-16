<!--
SYNC IMPACT REPORT
==================
Version change:    1.0.0 → 1.1.0
Bump rationale:    MINOR — materially changed guidance in one principle, nothing removed. The
                   component-test MUST "… → notification readable over HTTP" could not be met
                   before the read API exists (US-04), so US-01 and US-02 fell short of it (found by
                   /speckit-analyze on specs/002-order-confirmed-cancelled, finding C1). The rule
                   now applies once the read API exists, adds the interim rule (assert the stored
                   notification in MongoDB) and an explicit catch-up obligation for the story that
                   introduces the read API.

Modified principles:
  - VII. Testing Strategy — title unchanged. The component-test bullet now: full flow including
    the HTTP read once the read API exists; until then assert the stored notification directly in
    MongoDB; the story that introduces the read API MUST extend every existing component test with
    the HTTP read assertion.

Added sections:
  - (none)

Removed sections:
  - (none)

Templates reviewed:
  ✅ .specify/templates/plan-template.md  — Constitution Check gates derive from this file at runtime; no static change needed.
  ✅ .specify/templates/spec-template.md  — Generic structure; no change needed.
  ✅ .specify/templates/tasks-template.md — No principle-driven task type added or removed.
  ✅ .specify/templates/commands/*.md     — Directory not present in this project; nothing to update.

Runtime guidance:
  ✅ CLAUDE.md — "Tests" obligation reworded from a justified deviation to the v1.1.0 rule.
  ✅ specs/002-order-confirmed-cancelled/plan.md — Constitution Check VII back to ✅; Complexity
     Tracking entry closed by this amendment.
  ✅ README.md, Dockerfile, docs/adr — no reference to the changed rule; no change needed.

Migration note:
  - No code change. The component tests of US-01 and US-02 already assert in MongoDB, which is
    now the required interim behaviour. US-04 MUST add the HTTP read assertion to
    tests/component/test_order_placed_flow.py and tests/component/test_order_status_flow.py.

Previous amendments:
  - 1.0.0 (2026-11-16): initial ratification from the template, modelled on ecommerce-inventory
    v1.0.1 and adapted to Python 3.12 / FastAPI / MongoDB.

Deferred TODOs:
  (none)
-->

# ecommerce-notifications Constitution

`ecommerce-notifications` is the Python microservice that records customer notifications for order
events in a polyglot e-commerce system (Orders: NestJS, Payments: .NET, Inventory: Java). Sending
is simulated: each notification is stored in MongoDB with channel `email` and a log line is
written; there is no real email provider. The shared event contracts in
`ecommerce-infra/docs/events.md` are the source of truth for every topic and payload this service
consumes.

## Core Principles

### I. Domain-Driven Design & Layered Architecture

The codebase MUST follow a strict layered model:
**Messaging (Kafka consumers) and API (FastAPI routers) → Application service → Domain →
Repository**.

- **Consumers and routers** handle I/O only — deserialization, validation, and calling exactly
  one application service method. They MUST NOT contain logic.
- **Application services** orchestrate workflows. They MUST NOT contain business rules.
- **Domain** is the sole home of all business logic: aggregates, value objects, message
  templates and domain errors. The domain package MUST NOT import FastAPI, aiokafka, pymongo or
  Pydantic; it is plain Python.
- **Repositories** abstract persistence behind `typing.Protocol` interfaces owned by the
  application/domain layer. MongoDB documents MUST NOT leak into the domain: the domain model and
  the persistence documents are separate classes, mapped explicitly in the infrastructure layer.

Aggregates:
- `Notification` — `id`, `customerId`, `orderId`, `type`, `channel`, `message`, `status`,
  `createdAt`, `sourceEventId`.
- `OrderContext` — `orderId`, `customerId`, `totalAmount`, `recordedAt`.

Placing business logic in consumers, routers, application services or repositories is a
constitutional violation and MUST be rejected in code review.

### II. Event-Driven Service, Read-Only API (NON-NEGOTIABLE)

All business input MUST arrive as Kafka events. The HTTP surface is limited to:

- Health, liveness and readiness endpoints.
- Read-only notification queries:
  - `GET /notifications` — optional filters `customerId`, `orderId`, `type`; `limit`/`offset`
    pagination; results ordered newest first (`createdAt` descending).
  - `GET /notifications/{id}`.

Rules:
- The service MUST NOT expose any endpoint that creates, changes or deletes data.
- The service MUST NOT publish Kafka events.
- Adding any write endpoint or any produced event requires an accepted ADR in `docs/adr/`
  **before** implementation starts, followed by a constitution amendment.
- CORS allowed origins MUST come from an environment variable and MUST be empty by default.

### III. Write Flow Integrity (NON-NEGOTIABLE)

Every state change MUST follow this exact sequence and MUST NOT deviate:

1. Consume the Kafka message.
2. Deserialize and validate it into an inbound Pydantic model (Principle V).
3. The consumer calls one application service method.
4. The domain decides what to record.
5. Persist to **MongoDB**.
6. **Only after** a successful save: commit the consumer offset.

The shared MongoDB is a standalone instance without multi-document transactions, so every write in
step 5 MUST be idempotent by itself:
- `OrderContext` is **upserted** by `orderId`.
- A `Notification` is **inserted** under a **UNIQUE index on `sourceEventId`**, so that a
  redelivery after a crash is a recognised duplicate (duplicate-key error mapped to a no-op), not
  a failure and not a second notification.

If a save fails, the offset MUST NOT be committed. Committing an offset before the save is a
critical violation. The simulated send (the log line) MUST happen only after the notification is
stored, and never for a recognised duplicate.

### IV. Notification Integrity (NON-NEGOTIABLE)

- At most one notification MUST exist per `sourceEventId` (enforced by a unique index).
  Processing the same event twice MUST produce the same stored result.
- A notification is immutable once stored: no code path updates or deletes it.
- Notification types MUST be an enum:
  - `ORDER_RECEIVED` — from `orders.order-placed`.
  - `ORDER_CONFIRMED` — from `orders.order-confirmed`.
  - `ORDER_CANCELLED` — from `orders.order-cancelled`; the message includes the `reason` when
    present.
  - `PAYMENT_REFUNDED` — from `payments.payment-refunded`.
- Message texts MUST come from templates in the domain, MUST be deterministic (same event → same
  text), and MUST contain ids and amounts only — no personal data beyond `customerId`.
- `orders.order-placed` also stores the `OrderContext` (`orderId` → `customerId`,
  `totalAmount`).
- `payments.payment-refunded` carries no `customerId`; the customer MUST be resolved through the
  `OrderContext`. If the context does not exist yet, this is a **retryable failure** that goes
  through the consumer retry and dead-letter rules (Principle VIII) — never a silent skip and
  never a notification without a customer.
- All timestamps MUST be UTC, timezone-aware, and serialized as ISO 8601.

### V. Type Safety & Input Validation

- Python 3.12, managed by `uv` and pinned in `.python-version` and `requires-python`. The
  machine's global Python (3.11) MUST NOT be relied on.
- `mypy --strict` MUST pass with zero errors.
- `Any` is banned unless used at the narrowest scope with a written justification.
  `# type: ignore` MUST name the error code and carry a justification comment.
- Every inbound message and every HTTP response MUST be a Pydantic v2 model, frozen where
  possible. An invalid message MUST NOT be partially processed.
- Notification type, channel and status MUST be enums (`StrEnum`); bare string literals for these
  are prohibited.
- Domain errors MUST be typed exception classes (e.g. `OrderContextNotFoundError`). Bare
  `except:` is prohibited; `except Exception` without re-raise or explicit handling is
  prohibited. Exceptions MUST NOT be swallowed; no silent failures.
- Ruff MUST enable at least the rule sets `E`, `F`, `I`, `B`, `UP`, `ANN`, `S`, `C4`, `SIM`,
  `RUF`.

### VI. Test Coverage

All domain logic and application services MUST have unit tests.

- Every domain method and application service method MUST have at least one test covering the
  happy path and at least one covering an error/edge case.
- Domain tests MUST be pure pytest — no FastAPI, no database, no Kafka.
- Application service tests MAY use fakes or mocks for repositories.
- A PR that adds or changes domain or application behaviour without corresponding tests MUST NOT
  be merged.

### VII. Testing Strategy

Three test layers are required:

- **Unit tests** — domain and application layers in isolation.
- **Integration tests** — repositories and the unique indexes against a real MongoDB via
  Testcontainers (no mongomock or in-memory substitutes).
- **Component tests** — the full application with real MongoDB and real Kafka via
  Testcontainers. Other services are simulated by producing their events to Kafka. Once the read
  API exists, they MUST cover the complete flow: event consumed → notification stored →
  notification readable over HTTP. Until then they MUST assert the stored notification directly in
  MongoDB, and the story that introduces the read API MUST extend every existing component test
  with the HTTP read assertion in the same change set.

Tools: pytest, pytest-asyncio, pytest-cov, httpx for API tests, Testcontainers (MongoDB, Kafka).
`time.sleep` and bare `asyncio.sleep` waits in tests are prohibited; asynchronous assertions MUST
use a polling helper with a timeout.
Minimum coverage: **80%** line coverage on the domain and application packages, enforced with
`--cov-fail-under=80` in the gate — the run fails below the threshold.
Test names MUST describe the behaviour, e.g.
`test_should_not_create_a_second_notification_when_the_same_event_is_redelivered`.

### VIII. Kafka Events (Consumer)

Kafka is the event bus between services. This service **only consumes** events.
`ecommerce-infra/docs/events.md` is the source of truth for every topic and payload.

**Consumed topics:**
- `orders.order-placed` — record the `OrderContext` and an `ORDER_RECEIVED` notification.
- `orders.order-confirmed` — record an `ORDER_CONFIRMED` notification.
- `orders.order-cancelled` — record an `ORDER_CANCELLED` notification.
- `payments.payment-refunded` — record a `PAYMENT_REFUNDED` notification.

**Produced topics:**
- (none) — this service MUST NOT produce Kafka events (Principle II).

**Event payload rules:**
- Every event MUST include `eventId` (UUID), `occurredAt` (ISO 8601 timestamp), `aggregateId`
  (UUID) and `version` (integer).
- For consumed `orders.*` events, `aggregateId` is the order id and `customerId` is in the
  payload.
- For consumed `payments.*` events, `aggregateId` is the **payment id**; the order is identified
  by the separate `orderId` field, which MUST be used instead of `aggregateId`.
- Payloads are JSON. Unknown extra fields MUST be ignored; a missing required field makes the
  message invalid.
- Event models MUST carry a docstring for every field and MUST match
  `ecommerce-infra/docs/events.md`. Any change to an event contract MUST update that file in the
  same change set (a linked PR in `ecommerce-infra`).

**Consumer rules:**
- Consumers MUST be idempotent by `eventId` (Principles III and IV). Processing the same event
  twice MUST produce the same result.
- Offsets MUST be committed manually, only after the MongoDB save has succeeded.
- Failed processing MUST be retried with exponential backoff. Once retries are exhausted, the
  message MUST be stored in the inbound dead-letter collection (original payload, topic,
  partition, offset, error, attempts, timestamps), the failure logged with full context, and the
  offset committed so the partition is not blocked.
- Unparseable or invalid messages MUST go straight to the inbound dead-letter collection without
  retries, after which the offset is committed.
- Consumers MUST NOT contain business logic (Principle I).

**Startup resilience:**
- The service MUST start and answer health checks when Kafka is unreachable: no broker connection
  is required at boot, and consumers connect and reconnect in the background. Consumer state MUST
  be reported in the readiness endpoint without failing it.

### IX. Branching Strategy

This project follows a **feature-branch workflow** tied to user stories, identical to
ecommerce-inventory.

**Branch naming convention:**
- Feature branches: `feature/US-XX-short-description`
- Bug fixes: `fix/US-XX-short-description`
- Infrastructure: `chore/short-description`

Examples:
- `feature/US-01-order-received-notification`
- `feature/US-02-order-confirmed-notification`

**Rules:**
- `main` is always production-ready — it MUST NOT receive direct commits, except the initial
  repository setup.
- Every user story gets its own branch created from `main` before any implementation begins.
- Each branch maps to exactly one user story.
- PRs MUST reference the user story: "Implements US-01: Order received notification".
- Branches MUST be deleted after merge.
- Commit messages MUST follow Conventional Commits with a scope:
  `feat(US-01): add order confirmed notification`
  `test(US-01): add unit tests for notification aggregate`
  `docs(US-01): add docstrings to order placed event model`

**Automated branch creation (MANDATORY):**
When /speckit.specify is invoked for a user story, Claude Code MUST:
1. Ensure the working tree is clean (no uncommitted changes)
2. Checkout main and pull latest: `git checkout main && git pull`
3. Create and checkout the feature branch: `git checkout -b feature/US-XX-short-description`
4. Only then proceed with generating the spec

The branch MUST exist before any spec file is written.
Claude Code MUST NOT generate spec files while on main.

### X. Build & Code Quality Integrity (NON-NEGOTIABLE)

The project MUST pass all quality checks at all times.

**After every task implementation, Claude Code MUST run in this order, using `uv`:**

1. `uv run ruff check`          — lint MUST report zero violations
2. `uv run ruff format --check` — formatting MUST be consistent
3. `uv run mypy`                — strict type checking MUST report zero errors
4. `uv run pytest --cov`        — unit, integration and component tests and the coverage gate
   (`--cov-fail-under=80`) MUST pass

**Rules:**
- If any of the above fails, Claude Code MUST fix it before moving to the next task.
- A failing gate (lint, format, type or test failure) MUST never be committed to the branch.
- Ruff warnings and mypy errors are blocking — not deferred items.
- Claude Code MUST NOT mark a task as complete until all four checks pass.

**Tooling:**
- Project and environment management: `uv`
- Linter and formatter: Ruff
- Type checker: mypy (strict)
- Tests: pytest, pytest-asyncio, pytest-cov

**Configuration files required:**
- `pyproject.toml` — `requires-python = ">=3.12,<3.13"`; Ruff, mypy strict and pytest/coverage
  settings configured
- `uv.lock`
- `.python-version` — `3.12`
- `.editorconfig`
- `.gitignore` for Python

## Technology Stack

| Concern | Choice |
|---------|--------|
| Runtime | Python 3.12 (managed by `uv`) |
| Framework | FastAPI + uvicorn |
| Models & settings | Pydantic v2 + pydantic-settings |
| Messaging | Kafka via aiokafka |
| Persistence | MongoDB via the PyMongo async API (`AsyncMongoClient`) |
| Testing | pytest, pytest-asyncio, pytest-cov, httpx, Testcontainers (MongoDB, Kafka) |
| Lint / Format | Ruff |
| Types | mypy (strict) |
| Containerization | Docker (multi-stage `Dockerfile` on a slim `python:3.12` image, non-root user) |
| Local infrastructure | ecommerce-infra (docker compose): MongoDB `localhost:27017`, database `notifications`, user `mongo_user` / `mongo_pass`, `authSource=admin`, collections `notifications`, `order_context`, `inbound_dead_letters`; Kafka `localhost:9092` |

Indexes MUST be created at startup from a versioned module in the codebase; schema and index
changes MUST be made only through code in that module — ad-hoc scripts or manual changes are
prohibited.
Introducing a new runtime dependency that duplicates a capability already provided by the stack
above requires an ADR in `docs/adr/` before the PR can be merged.

## Documentation Standards

All public modules, classes and functions MUST carry docstrings.
Each top-level package MUST include a module docstring in its `__init__.py` describing its
responsibility.
FastAPI endpoints and models MUST carry descriptions so the generated OpenAPI document is
complete.
Kafka event payloads MUST be documented in the event models (every field) and in
`ecommerce-infra/docs/events.md`, which remains the source of truth.
Architectural decisions MUST be recorded as ADRs in `docs/adr/` using the standard template
(Context / Decision / Consequences).
ADRs are numbered sequentially with a four-digit prefix (`0001-short-title.md`, `0002-...`), are
never renumbered, and are indexed in `docs/adr/README.md`. A superseded ADR is kept and marked as
superseded by the newer one.
The repository MUST keep `README.md`, `CLAUDE.md` and the multi-stage `Dockerfile` up to date with
the current behaviour and this constitution.

## Governance

This constitution supersedes all other project conventions and style guides. When a conflict
arises, the constitution takes precedence.

**Amendment procedure**: Any amendment requires (1) a draft PR updating this file, (2) a version
bump per the policy below, (3) approval from at least one other maintainer, and (4) a migration
note if existing code must change.

**Versioning policy**:
- MAJOR: Removal or redefinition of an existing principle (breaking governance change).
- MINOR: Addition of a new principle or materially expanded guidance.
- PATCH: Clarifications, wording refinements, typo fixes, non-semantic changes.

**Compliance**: Every PR review MUST verify that no constitutional principle is violated.
Reviewers are empowered to block merges for violations without exception. New principles take
effect immediately upon merge.

**Version**: 1.1.0 | **Ratified**: 2026-11-16 | **Last Amended**: 2026-11-16
