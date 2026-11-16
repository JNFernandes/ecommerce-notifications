# Implementation Plan: Payment refunded notification (US-03)

**Branch**: `feature/US-03-payment-refunded-notification` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/003-payment-refunded-notification/spec.md`, plus the
planning input (reuse the US-01/US-02 infrastructure; parser, service with a typed retryable error,
domain template; settings and README).

## Summary

The consumer gets a fourth route: `payments.payment-refunded` → `parse_payment_refunded` →
`RecordPaymentRefundedService.handle`. The service:
- looks up the `OrderContext` by the event's `orderId` (never `aggregateId`, the payment id);
- raises `OrderContextNotFoundError` when it is missing, which the existing processor retries with
  backoff and then dead-letters;
- otherwise records an immutable `PAYMENT_REFUNDED` notification for the context's customer, with
  the message `Your payment of <amount> <currency> for order <orderId> was refunded.`, through the
  shared store-then-send step.

Idempotency, commit after save and dead letters are unchanged.

The supporting changes are small:
- a generic `EventEnvelope` under the order envelope;
- a read method `get` on the order-context repository;
- a generic `InvalidAmountError` with `InvalidOrderTotalError` as a subclass, plus
  `InvalidCurrencyError`;
- one more topic setting.

## Technical Context

**Language/Version**: Python 3.12 (uv)

**Primary Dependencies**: FastAPI, Pydantic v2, pydantic-settings, aiokafka 0.15, PyMongo 4.20
async API. No new dependency.

**Storage**: MongoDB; `notifications` (writes), `order_context` (reads only), `inbound_dead_letters`.
No index change (`INDEX_SCHEMA_VERSION` 2).

**Testing**: pytest, pytest-asyncio, pytest-cov, httpx, Testcontainers (`mongo:7`,
`confluentinc/cp-kafka:7.6.1`)

**Target Platform**: Linux container; local development on Windows with uv

**Project Type**: Event-driven web service (one Kafka consumer plus a read-only HTTP API)

**Performance Goals**: A refund is recorded within 5 s (SC-003). A missing context is
dead-lettered within the retry budget (about 15 s with the defaults, SC-004).

**Constraints**:
- One consumer, one message at a time per instance (US-01 Q2), which causes the documented
  catch-up limitation.
- The customer comes only from the order context.

**Scale/Scope**: Four consumed topics, one consumer group.

There is no `NEEDS CLARIFICATION`. The spec was clarified (four answers), and the producer's JSON
was checked (research R1).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | How this plan complies | Status |
|---|---|---|---|
| I | DDD & layers | The route = parser + one service method. `RecordPaymentRefundedService` orchestrates: lookup, raise or build through the domain factory, store and send. The template, the currency and amount rules and the factory are in `domain`. The order context lookup goes through the `OrderContextRepository` port, with the Mongo adapter in `infrastructure`. | ✅ |
| II | Read-only API, no events | No endpoint, nothing published. | ✅ |
| III | Write flow | parse → validate → one service call → lookup → domain → insert (idempotent under the unique index) → send log → commit. No write when the context is missing. | ✅ |
| IV | Notification integrity | `PAYMENT_REFUNDED` enum value; immutable; deterministic template with ids, amount and currency; `payments.payment-refunded` resolves the customer through `OrderContext`; a missing context is a retryable failure, never a skip, never a notification without a customer. These are exactly Principle IV's words. | ✅ |
| V | Types & validation | Frozen Pydantic model with field docstrings and JSON-type guards; `Decimal` amount; typed `OrderContextNotFoundError`, `InvalidAmountError` and `InvalidCurrencyError`; mypy strict. | ✅ |
| VI | Test coverage | Happy-path and edge tests for the template, factory, service (found / missing / duplicate / error) and parser. | ✅ |
| VII (v1.1.0) | Testing strategy | Unit, integration (`get` + unique index) and component tests (real Kafka + MongoDB: place→refund, missing context → retries → dead letter, redelivery, invalid). The component tests check MongoDB, as v1.1.0 requires until the read API exists. | ✅ |
| VIII | Kafka consumer | For a `payments.*` event, `aggregateId` is the payment id and the order comes from `orderId`; unknown fields are ignored; the same idempotency, commit, retry and dead-letter rules apply; `events.md` gets a linked PR. | ✅ |
| IX | Branching | `feature/US-03-payment-refunded-notification` from `main`; `feat(US-03)` / `test(US-03)` / `docs(US-03)`. | ✅ |
| X | Gate | Four gate commands per task; every commit green. | ✅ |
| Stack / Docs | No dependency or index change. README (topic row, setting, known-limitation note), CLAUDE.md and `events.md`. No ADR: ADR 0003 already decides "consume order-placed to know the refund customer". | ✅ |

**Post-design re-check (after Phase 1)**: all ✅. One design point needed care: the component test
"place then refund" must wait for the order context before producing the refund. Otherwise the
catch-up limitation can turn the happy path into a dead letter. The quickstart does the same.

## Project Structure

### Documentation (this feature)

```text
specs/003-payment-refunded-notification/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R9
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── payments-payment-refunded.consumer.md
│   └── configuration.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
src/notifications/
├── main.py                              # + RecordPaymentRefundedService, 4th route in build_routing_table
├── domain/
│   ├── errors.py                        # + InvalidAmountError (base of InvalidOrderTotalError), InvalidCurrencyError
│   ├── messages.py                      # + require_currency_code, payment_refunded_message; format_amount → InvalidAmountError
│   └── notification.py                  # + PAYMENT_REFUNDED, Notification.payment_refunded
├── application/
│   ├── errors.py                        # NEW OrderContextNotFoundError
│   ├── ports.py                         # + OrderContextRepository.get
│   └── record_payment_refunded.py       # NEW PaymentRefundedCommand, RecordPaymentRefundedService
├── messaging/
│   ├── events.py                        # CHANGED EventEnvelope base; OrderEventEnvelope extends it
│   └── payment_refunded.py              # NEW PaymentRefundedEvent, parse_payment_refunded
└── infrastructure/
    ├── settings.py                      # + kafka_topic_payment_refunded (distinct-topics validator)
    └── repositories/order_contexts.py   # + get

tests/
├── support/kafka.py                     # ORDER_TOPICS → CONSUMED_TOPICS (+ payments.payment-refunded)
├── support/fakes.py                     # InMemoryOrderContextRepository.get
├── support/events.py                    # + payment_refunded_payload
├── unit/domain/                         # + template, currency, amount errors, factory
├── unit/application/                    # + record_payment_refunded
├── unit/messaging/                      # + payment_refunded parsing; envelope tests unchanged
├── unit/infrastructure/                 # + topic setting
├── unit/test_main_routes.py             # + 4th route
├── integration/                         # + order context get; refund under unique index
└── component/test_payment_refunded_flow.py   # NEW place→refund, missing context → DLQ, redelivery, invalid
```

**Structure Decision**: The same single project and layers. One new application module, one new
messaging module and `application/errors.py`; everything else extends existing files.

### Processing flow

```text
KafkaConsumerManager → RetryingMessageProcessor → RoutingTable.handle
  payments.payment-refunded → parse_payment_refunded (invalid → dead letter, 1 attempt)
    → RecordPaymentRefundedService.handle(command)
        context = order_contexts.get(command.order_id)        # orderId, never aggregateId
        None  → raise OrderContextNotFoundError                # retried with backoff → dead letter
        found → Notification.payment_refunded(customer_id=context.customer_id, …)
              → record_and_send (unique sourceEventId; send log only when stored)
  HANDLED → commit(offset + 1)
```

## Complexity Tracking

No constitution violations to justify.
