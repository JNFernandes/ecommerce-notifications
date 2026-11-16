# Implementation Plan: Order received notification (US-01)

**Branch**: `feature/US-01-order-received-notification` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-order-received-notification/spec.md`

## Summary

The service starts consuming `orders.order-placed`. For each valid event it:
1. upserts an `OrderContext` (first write wins),
2. inserts one immutable `ORDER_RECEIVED` notification under a unique `sourceEventId`,
3. logs a simulated email send, and only then commits the offset.

Invalid messages are dead-lettered at once. Other failures are retried with capped exponential
backoff and then dead-lettered. If the dead-letter store itself is down, the consumer holds the
message and never moves past it (clarification Q1). Messages are processed one at a time per
instance (Q2).

The technical approach follows from three verified findings (research R1–R3):
- **Exact amounts**: payloads are parsed with the stdlib `json` module and `Decimal`, then
  validated by Pydantic with JSON-type guards, so amounts stay exact.
- **Explicit mapping**: amounts are stored as `Decimal128`, mapped explicitly in infrastructure.
- **Idempotency**: every write is idempotent on its own (ADR 0002).

## Technical Context

**Language/Version**: Python 3.12 (uv, `.python-version`)

**Primary Dependencies**: FastAPI, Pydantic v2, pydantic-settings, aiokafka 0.15, PyMongo 4.20
async API. No new runtime dependency.

**Storage**: MongoDB 7 (shared, standalone, no transactions). Database `notifications`,
collections `notifications`, `order_context`, `inbound_dead_letters`.

**Testing**: pytest, pytest-asyncio, pytest-cov, httpx, Testcontainers (`mongo:7`,
`confluentinc/cp-kafka:7.6.1`)

**Target Platform**: Linux container (`python:3.12-slim`); local development on Windows with uv

**Project Type**: Event-driven web service (Kafka consumer plus a read-only HTTP API)

**Performance Goals**:
- A notification is recorded within 5 s of placement (SC-002).
- Throughput is not a goal; the volume is that of a demo shop.

**Constraints**:
- One message at a time per instance.
- The retry budget is about 15 s per failing message with the defaults.
- No multi-document transactions.
- The service must start with Kafka unreachable.

**Scale/Scope**: 3 partitions, 1–3 instances. One topic in this story; three more follow in US-02
and US-03 through the same handler/processor structure.

No `NEEDS CLARIFICATION` remains. The spec was clarified (Q1, Q2) and the technical unknowns are
resolved in [research.md](research.md).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | How this plan complies | Status |
|---|---|---|---|
| I | DDD & layers | The consumer handler only parses and maps to a command, then calls `RecordOrderReceivedService.handle` (one method). Business rules live in `domain`: message template, amount rule, conflict rule, factories. Repositories, sender, clock and id generator are `Protocol` ports in `application`; Mongo documents are mapped in `infrastructure`. Retry and dead-letter policy is message-delivery plumbing in `messaging`, not business logic. | ✅ |
| II | Read-only API, no events | No new endpoint; nothing is published. | ✅ |
| III | Write flow | Parse → validate → one service call → domain → upsert context → insert notification → log send → commit offset. The commit happens only after `HANDLED`. Each write is idempotent alone (R3). | ✅ |
| IV | Notification integrity | Unique `sourceEventId` index; immutable aggregate; `StrEnum` type; deterministic template with ids and amount only; `OrderContext` stored; UTC-aware timestamps. | ✅ |
| V | Types & validation | mypy strict; frozen Pydantic inbound model; JSON-type guards (R1); `StrEnum`s; typed domain errors and `InvalidMessageError`. A single `except Exception` in `MessageProcessor` *handles* the error (retry, dead letter, log), which is the case Principle V permits. `Any` stays confined to the aiokafka boundary (existing justified override). | ✅ |
| VI | Test coverage | Every domain and application method has happy-path and edge tests (see the test plan). | ✅ |
| VII | Testing strategy | Unit (domain, application, messaging with a fake sleeper); integration (repositories and unique indexes on real Mongo); component (app + Mongo + Kafka containers, pause/unpause for outages). `wait_until` only, no sleeps; ≥ 80% on domain and application. | ✅ |
| VIII | Kafka consumer | Topic `orders.order-placed`; `aggregateId` is the order id; unknown fields ignored; field docstrings; idempotent by `eventId`; manual commit after the save; backoff; dead letter with full context; invalid → dead letter; startup resilience kept; `events.md` updated in a linked PR (R11). | ✅ |
| IX | Branching | `feature/US-01-order-received-notification` from `main`; commits `feat(US-01): …`, `test(US-01): …`, `docs(US-01): …`. | ✅ |
| X | Gate | Four gate commands after every task. | ✅ |
| Stack | Indexes in the versioned module | `INDEX_SCHEMA_VERSION` 1 → 2 with three indexes; no scripts; no new dependency (backoff is hand-written, R6). | ✅ |
| Docs | Docstrings, OpenAPI, ADRs, README/CLAUDE.md | Docstrings on all new public code; README configuration table and topic status; CLAUDE.md "Where things are"; no new ADR needed (ADR 0002 and 0003 already cover the decisions). | ✅ |

**Post-design re-check (after Phase 1)**: still all ✅. The design added no layer, endpoint or
dependency. Two points were checked explicitly:
- `DeadLetterStore` is a port owned by `messaging`, not `application`. Dead-lettering is a
  delivery concern, and no application service uses it. Its adapter lives in `infrastructure`,
  and `main.py` wires it.
- `order_context` uses the order id as `_id` instead of a separate unique index. This satisfies
  FR-020 ("unique order context by order id") with one index fewer.

## Project Structure

### Documentation (this feature)

```text
specs/001-order-received-notification/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R11
├── data-model.md        # domain, application ports, inbound model, documents, indexes
├── quickstart.md        # manual validation against ecommerce-infra
├── contracts/
│   ├── orders-order-placed.consumer.md
│   ├── configuration.md
│   └── observability.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks (not created here)
```

### Source Code (repository root)

```text
src/notifications/
├── main.py                              # + wire repositories, sender, service, processor, handler
├── domain/
│   ├── errors.py                        # NEW DomainError, InvalidOrderTotalError, NaiveTimestampError
│   ├── messages.py                      # NEW order_received_message, amount formatting
│   ├── notification.py                  # NEW NotificationType/Channel/Status, Notification
│   └── order_context.py                 # NEW OrderContext
├── application/
│   ├── health.py                        # unchanged
│   ├── ports.py                         # NEW repository/sender/clock/id Protocols
│   └── record_order_received.py         # NEW OrderPlacedCommand, RecordOutcome, service
├── messaging/
│   ├── consumer_manager.py              # CHANGED subscribe + consume loop + commit; stop races connect
│   ├── messages.py                      # NEW ConsumedMessage, InvalidMessageError, JSON decoding (R1)
│   ├── dead_letters.py                  # NEW DeadLetter, DeadLetterStore Protocol
│   ├── retry.py                         # NEW RetryPolicy, Sleeper Protocol, StopAwareSleeper
│   ├── processor.py                     # NEW MessageProcessor (retry, dead letter, hold), ProcessResult
│   └── order_placed.py                  # NEW OrderPlacedEvent (Pydantic) + OrderPlacedHandler
└── infrastructure/
    ├── indexes.py                       # CHANGED INDEXES, version 2
    ├── settings.py                      # CHANGED retry settings + validation
    ├── system.py                        # NEW SystemClock, Uuid4Generator
    ├── sender.py                        # NEW LoggingNotificationSender
    └── repositories/
        ├── __init__.py                  # NEW package docstring
        ├── notifications.py             # NEW MongoNotificationRepository + mapping
        ├── order_contexts.py            # NEW MongoOrderContextRepository + mapping
        └── dead_letters.py              # NEW MongoDeadLetterStore + mapping

tests/
├── unit/
│   ├── domain/                          # NEW messages, notification, order_context
│   ├── application/                     # + record_order_received (fakes)
│   ├── messaging/                       # + order_placed parsing, retry policy, processor, manager
│   └── infrastructure/                  # + settings validation
├── integration/                         # NEW Mongo container: repositories, indexes, Decimal128, dedupe
├── component/                           # + Kafka container: consume → store; redelivery; invalid; outage
└── support/                             # + fakes (repos, sender, clock, ids, sleeper), event builders, Kafka helpers
```

**Structure Decision**: Single project, the existing src layout from the bootstrap. New code goes
into the existing layer packages; persistence adapters are grouped under
`infrastructure/repositories/`. An `integration` test package is added, as Principle VII
requires.

### Processing flow (one message)

```text
ConsumerManager.getmany(max_records=1)
  └─ MessageProcessor.process(ConsumedMessage) ──────────────────────────────┐
       loop (hold, Q1):                                                       │
         try handler.handle(msg)  ──► OrderPlacedHandler                      │
                                       decode + validate (InvalidMessageError)│
                                       RecordOrderReceivedService.handle(cmd) │
                                         domain: OrderContext, Notification   │
                                         order_contexts.add_if_absent         │
                                         notifications.add → sender.send      │
           InvalidMessageError → dead letter (attempts 1)                     │
           other error → retry ≤ max_attempts with backoff → dead letter      │
         dead letter stored/exists → HANDLED                                  │
         dead-letter store failed  → sleep(max_delay) and loop                │
         stop requested            → STOPPED                                  │
  HANDLED → commit(offset + 1)   STOPPED → no commit ◄────────────────────────┘
```

### Test plan (by layer)

- **Domain**:
  - message formatting (129.9 → 129.90, 0, 10.005 → 10.01, 1E+2, large values);
  - factory rejections (negative total, NaN, naive time);
  - millisecond truncation;
  - `conflicts_with` (same, different customer, different total, recorded_at ignored);
  - immutability.
- **Application** (fakes):
  - records and sends once;
  - a duplicate does not send and returns `ALREADY_RECORDED`;
  - the context is written before the notification;
  - an existing context is kept, with a warning on conflict and none when equal;
  - a repository error propagates and nothing is sent.
- **Messaging (unit)**:
  - parsing table from research R1 (valid; each missing or invalid field; `version` true/1.0/"1"/2;
    amount string/bool/negative/NaN/int; bad UTF-8; bad JSON; JSON array; null value; extra
    fields);
  - `RetryPolicy` delays and cap;
  - processor paths: success; transient failure then success; exhausted → dead letter with
    attempts and first-failed time; invalid → dead letter once; dead letter already exists;
    dead-letter store failure → hold then success; stop during backoff → `STOPPED`;
  - manager: commit only on `HANDLED`; commit failure is logged; existing outage tests.
- **Infrastructure (unit)**: settings defaults and validation of the four new variables.
- **Integration** (Mongo container):
  - `ensure_indexes` creates the 3 indexes and is idempotent;
  - notification round trip (UUID strings, aware UTC dates);
  - duplicate `sourceEventId` → `False`; another duplicate key error still raises;
  - context `add_if_absent` returns `None`, then the existing context; `Decimal128` exactness;
  - concurrent upserts of one order;
  - dead letter round trip, dedupe, base64 for non-UTF-8 payloads.
- **Component** (Mongo + Kafka containers, real lifespan):
  - event → notification + context + one send log;
  - the same event twice → one notification;
  - an invalid message followed by a valid one → one dead letter + one notification;
  - Kafka paused at start, then unpaused → the event is processed without a restart, and
    readiness goes from not connected to `CONNECTED`.

## Complexity Tracking

No constitution violations to justify.
