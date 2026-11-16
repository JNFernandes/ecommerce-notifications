# Implementation Plan: Order confirmed and order cancelled notifications (US-02)

**Branch**: `feature/US-02-order-confirmed-cancelled-notifications` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-order-confirmed-cancelled/spec.md`, plus the
planning input (reuse US-01 infrastructure, a topic routing table, topic names in settings).

## Summary

The existing consumer also subscribes to `orders.order-confirmed` and `orders.order-cancelled`
and records an `ORDER_CONFIRMED` or `ORDER_CANCELLED` notification per event. The cancellation
message includes the normalised reason when the event has one. Everything that makes US-01
reliable is reused as is: manual commit after save, retries with backoff, dead letters, hold on
dead-letter failure, the unique `sourceEventId` index, and startup without Kafka.

The structural change is a **topic routing table**: `Route(topic, parse, handle)` entries
connect each topic to its parser and exactly one application service method, so a new topic is
a registration, not new consumer code. The order event envelope and the JSON-type guards move
to one shared module. Topic names become settings, with the contract names as defaults.

## Technical Context

**Language/Version**: Python 3.12 (uv)

**Primary Dependencies**: FastAPI, Pydantic v2, pydantic-settings, aiokafka 0.15, PyMongo 4.20
async API. No new dependency.

**Storage**: MongoDB, collection `notifications`. No schema or index change
(`INDEX_SCHEMA_VERSION` stays 2).

**Testing**: pytest, pytest-asyncio, pytest-cov, httpx, Testcontainers (`mongo:7`,
`confluentinc/cp-kafka:7.6.1`)

**Target Platform**: Linux container; local development on Windows with uv

**Project Type**: Event-driven web service (one Kafka consumer plus a read-only HTTP API)

**Performance Goals**: A notification is recorded within 5 s of the event (SC-002); volume is low.

**Constraints**:
- One consumer, one message at a time per instance.
- No order context use for these topics.
- No multi-document transactions.

**Scale/Scope**: Three order topics, 3 partitions each, one consumer group.

There is no `NEEDS CLARIFICATION`. The spec was clarified (reason included, non-string reason
dead-lettered, one consumer with a routing table), and the planning input is resolved in
[research.md](research.md) R1–R8.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | How this plan complies | Status |
|---|---|---|---|
| I | DDD & layers | Each `Route` = parser (messaging, I/O only) + exactly one service method; no logic in the route. `RecordOrderStatusService` orchestrates only (factory, then `record_and_send`). The templates, the reason normalisation and the factories are in `domain`, as plain Python. | ✅ |
| II | Read-only API, no events | No endpoint, nothing published. | ✅ |
| III | Write flow | Unchanged pipeline: parse → validate → one service call → domain → insert → send log → commit. A single insert under the unique index is idempotent. | ✅ |
| IV | Notification integrity | Enum types `ORDER_CONFIRMED` / `ORDER_CANCELLED`; immutable; deterministic templates; the reason is included when present (constitution text); UTC timestamps; one notification per `sourceEventId`. | ✅ |
| V | Types & validation | Frozen Pydantic models with field docstrings and JSON-type guards; typed `InvalidMessageError`; generic `Route[C]` keeps parser output and service input type-checked under mypy strict. | ✅ |
| VI | Test coverage | Happy-path and edge tests for every new domain function, factory and service method. | ✅ |
| VII | Testing strategy | Unit + integration (redelivery under the unique index) + component (real Kafka and MongoDB: each event, redelivery, invalid → dead letter); `eventually` / `wait_until` only. Component tests assert the stored notification in MongoDB, as constitution v1.1.0 requires until the read API exists (US-04 adds the HTTP read assertion). Originally recorded as a justified deviation (analysis finding C1); resolved by the v1.1.0 amendment. | ✅ |
| VIII | Kafka consumer | New topics with `aggregateId` = order id; unknown fields ignored; the same idempotency, commit, retry and dead-letter rules; a message on an unrouted topic is dead-lettered, not dropped; `events.md` updated in a linked PR (R8). | ✅ |
| IX | Branching | `feature/US-02-order-confirmed-cancelled-notifications` from `main`; `feat(US-02)` / `test(US-02)` / `docs(US-02)` commits. | ✅ |
| X | Gate | Four gate commands after every task; every commit green. | ✅ |
| Stack / Docs | No dependency; no index change; README (topics table, settings), CLAUDE.md and `events.md` updated. No new ADR: the routing table is an internal refactor of the existing consumer (no new technology, no contract change). | ✅ |

**Post-design re-check (after Phase 1)**: all ✅. VII was first recorded as a justified deviation
and is resolved by constitution v1.1.0 (see Complexity Tracking). The behaviour visible to US-01 changes in two
places:
- the duplicate log text becomes the generic `Event already recorded: type=…` (contracts/observability.md);
- `OrderPlacedHandler` is replaced by a route.

Both are covered by updating the existing US-01 tests; the `orders.order-placed` behaviour is
identical.

## Project Structure

### Documentation (this feature)

```text
specs/002-order-confirmed-cancelled/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R8
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── orders-order-confirmed-cancelled.consumer.md
│   ├── configuration.md
│   └── observability.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
src/notifications/
├── main.py                              # CHANGED build RoutingTable from settings (3 routes)
├── domain/
│   ├── messages.py                      # + order_confirmed_message, order_cancelled_message, normalise_reason
│   └── notification.py                  # + ORDER_CONFIRMED/ORDER_CANCELLED, factories, shared _create
├── application/
│   ├── recording.py                     # NEW RecordOutcome, record_and_send
│   ├── record_order_received.py         # CHANGED uses record_and_send (no behaviour change)
│   └── record_order_status.py           # NEW commands + RecordOrderStatusService (2 methods)
├── messaging/
│   ├── events.py                        # NEW JSON guards, OrderEventEnvelope, parse_event
│   ├── order_placed.py                  # CHANGED OrderPlacedEvent(OrderEventEnvelope), parser returns command; handler class removed
│   ├── order_status.py                  # NEW OrderConfirmedEvent, OrderCancelledEvent, parsers
│   ├── routing.py                       # NEW Route, RoutingTable
│   └── consumer_manager.py              # CHANGED takes RoutingTable instead of handlers
└── infrastructure/
    └── settings.py                      # + kafka_topic_order_* (3), distinct-topics validator

tests/
├── unit/domain/                         # + templates, normalise_reason, factories
├── unit/application/                    # + record_order_status, recording
├── unit/messaging/                      # + events/order_status parsing, routing; manager tests adapted
├── unit/infrastructure/                 # + topic settings
├── integration/                         # + redelivered confirmed/cancelled under the unique index
└── component/                           # + order status flow (each event, redelivery, invalid → DLQ)
```

**Structure Decision**: The same single project and layers as US-01. New code goes into the
existing packages; no new package.

### Processing flow (unchanged pipeline, new routing step)

```text
KafkaConsumerManager (subscribes to routing.topics)
  └─ RetryingMessageProcessor.process(message, routing)       # retries / dead letter / hold
       └─ RoutingTable.handle(message)      # the one MessageHandler
            route = table[message.topic]   (missing → InvalidMessageError → dead letter)
            command = route.parse(message) (invalid → InvalidMessageError → dead letter)
            await route.handle(command)    # exactly one service method:
               orders.order-placed    → RecordOrderReceivedService.handle
               orders.order-confirmed → RecordOrderStatusService.record_confirmed
               orders.order-cancelled → RecordOrderStatusService.record_cancelled
  HANDLED → commit(offset + 1)
```

## Complexity Tracking

No open constitution violations.

| Violation | Resolution |
|-----------|------------|
| Principle VII (v1.0.0): component tests MUST read the notification back over HTTP, but the read API is US-04 (out of scope here); the same gap existed in US-01. | Resolved by constitution **v1.1.0** (2026-11-16): the HTTP read applies once the read API exists; until then component tests assert in MongoDB, and US-04 MUST extend every existing component test with the HTTP read assertion. |
