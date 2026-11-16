---

description: "Task list for US-03 payment refunded notification"
---

# Tasks: Payment refunded notification (US-03)

**Input**: Design documents from `specs/003-payment-refunded-notification/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R9), data-model.md, contracts/, quickstart.md

**Tests**: Required (constitution Principles VI and VII v1.1.0; requested in the planning input).

**Gate (every task)**: each task ends with the full quality gate, in this order:
`uv run ruff check` → `uv run ruff format --check` → `uv run mypy` → `uv run pytest --cov`. A task
is done only when all four pass. Write the task's test first and see it fail, then implement,
then run the gate. **One task = one commit** (`feat(US-03): …`, `test(US-03): …`,
`refactor(US-03): …`, `docs(US-03): …`). Never commit a red gate.

**Order** (planning input): domain template → parser → application method with the typed
retryable error → topic registration → integration and component tests → docs. Shared
prerequisites come first (Phases 1–2), each in that layer order.

**Lessons from US-02, built into the order**:
- The test broker must create every topic the consumer subscribes to **before** the route is
  registered (T002 before T009); otherwise unrelated component tests stall.
- A protocol change and its last user change in the same task, so every commit is green.

**Story labels**: `[US1]` refund recorded; `[US2]` missing order context → retries → dead letter;
`[US3]` reliability of the new topic. They are inside the single backlog item US-03.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the spec user story the task belongs to

---

## Phase 1: Setup (test support)

**Purpose**: Test helpers for the refund topic. No production change.

- [X] T001 [P] In `tests/support/events.py`, add `PAYMENT_REFUNDED_TOPIC = "payments.payment-refunded"`
  and `payment_refunded_payload(**overrides) -> dict[str, object]`. It returns a valid refund:
  - fresh `eventId`, `aggregateId` (the payment id) and `orderId`, with `aggregateId != orderId`;
  - `occurredAt` and `refundedAt` as ISO 8601 with `+00:00`;
  - `version` 1, `amount` `Decimal("129.90")`, `currency` `"EUR"`;
  - `MISSING` removes a key.

  No test uses it yet. Gate.
- [X] T002 [P] In `tests/support/kafka.py`, rename `ORDER_TOPICS` to `CONSUMED_TOPICS`, add
  `"payments.payment-refunded"`, and rename `create_order_topics` to `create_consumed_topics`
  (same behaviour). Update every caller:
  - `tests/component/test_order_placed_flow.py`
  - `tests/component/test_order_status_flow.py`
  - `tests/component/test_dead_letters_flow.py`
  - `tests/component/test_kafka_outage.py`

  The topic exists in ecommerce-infra already (Payments publishes it). Gate.

---

## Phase 2: Foundational (behaviour-preserving)

**Purpose**: The pieces the refund needs, without changing any existing behaviour.

**⚠️ CRITICAL**: No user story work starts before this phase is complete.

- [X] T003 [P] Domain errors and amounts, in `src/notifications/domain/errors.py` and
  `src/notifications/domain/messages.py`:
  - add `InvalidAmountError(DomainError)`, make `InvalidOrderTotalError(InvalidAmountError)`, and
    add `InvalidCurrencyError(DomainError)`;
  - `format_amount` raises `InvalidAmountError` (through a new `require_valid_amount`);
  - `require_valid_total` keeps raising `InvalidOrderTotalError`;
  - `order_received_message` calls `require_valid_total` before formatting.

  Update the one test that calls `format_amount` directly
  (`tests/unit/domain/test_messages.py`, negative / NaN / Infinity) to expect
  `InvalidAmountError`. Add a test that an order total still raises `InvalidOrderTotalError`
  through `order_received_message`. All other existing tests stay unchanged and green. Gate.
- [X] T004 [P] Envelope split, in `src/notifications/messaging/events.py`:
  - add `EventEnvelope` (frozen, `extra="ignore"`, `use_attribute_docstrings=True`) with
    `eventId`, `aggregateId` (docstring: the id of the aggregate that raised the event; for
    `payments.*` the payment, never the order), `occurredAt` and `version`;
  - `OrderEventEnvelope(EventEnvelope)` adds only `customerId`, with the existing docstring and
    behaviour.

  Tests in `tests/unit/messaging/test_events.py`: a payload without `customerId` is valid for
  `EventEnvelope` and invalid for `OrderEventEnvelope`. All existing envelope and order parser
  tests stay green. Gate.
- [X] T005 [P] Order context lookup, through the port, the fake and the adapter together:
  - in `src/notifications/application/ports.py`, add
    `async get(order_id: UUID) -> OrderContext | None` to `OrderContextRepository`;
  - in `tests/support/fakes.py`, add `InMemoryOrderContextRepository.get`;
  - in `src/notifications/infrastructure/repositories/order_contexts.py`, add
    `MongoOrderContextRepository.get`, which runs `find_one({"_id": str(order_id)})` and maps
    with `from_document`.

  Integration tests in `tests/integration/test_order_context_repository.py`: `get` returns the
  stored context (exact `Decimal("123456789012345678.123456789")`, aware `recordedAt`); `get` of
  an unknown order → `None`. Gate.

**Checkpoint**: The gate is green and there is no behaviour change. Topics, envelope, errors and
lookup are ready.

---

## Phase 3: User Story 1 — Customer is told their payment was refunded (Priority: P1) 🎯 MVP

**Goal**: A refund for an order with an order context → one `PAYMENT_REFUNDED` notification for the
context's customer, then the send log line.

**Independent Test**: Produce a placement, wait for its context, produce a refund for the same
`orderId` with a different `aggregateId` → one notification with
`Your payment of 129.90 EUR for order <orderId> was refunded.` and the placement's customer.

- [X] T006 [P] [US1] Domain:
  - in `src/notifications/domain/messages.py`, add `require_currency_code(code) -> str`
    (`^[A-Z]{3}$`, otherwise `InvalidCurrencyError`) and
    `payment_refunded_message(order_id, amount, currency) -> str`
    (`Your payment of <format_amount(amount)> <currency> for order <order_id> was refunded.`);
  - in `src/notifications/domain/notification.py`, add `NotificationType.PAYMENT_REFUNDED` and
    `Notification.payment_refunded(*, notification_id, customer_id, order_id, amount, currency,
    source_event_id, now)` through `_create`.

  Tests:
  - `tests/unit/domain/test_messages.py`: `129.9`/`EUR` → `129.90 EUR`; `0`/`USD` → `0.00 USD`;
    `10.005` → `10.01`; a large exact amount; `eur` / `EURO` / `""` / `E1R` →
    `InvalidCurrencyError`; a negative amount → `InvalidAmountError`; deterministic;
  - `tests/unit/domain/test_notification.py`: the factory's fields and type; naive `now` → error;
    the enum value list now ends with `PAYMENT_REFUNDED`. Gate.
- [X] T007 [P] [US1] Parser:
  - create `src/notifications/application/record_payment_refunded.py` with
    `PaymentRefundedCommand` only (frozen dataclass: `event_id`, `order_id`, `amount: Decimal`,
    `currency: str`);
  - create `src/notifications/messaging/payment_refunded.py` with
    `PaymentRefundedEvent(EventEnvelope)` and `parse_payment_refunded(message) ->
    PaymentRefundedCommand`. The event adds `orderId: JsonUuid`, `amount` (`json_number`,
    `ge=0`), `currency` (`json_string`, `pattern="^[A-Z]{3}$"`) and `refundedAt` (aware, JSON
    string), each with a field docstring from `events.md`. `to_command` uses `order_id`, never
    `aggregate_id`.

  Tests in `tests/unit/messaging/test_payment_refunded.py`:
  - valid → command with `order_id == orderId`, and `!= aggregateId`;
  - a `customerId` in the payload is ignored;
  - each of `eventId`, `aggregateId`, `occurredAt`, `version`, `orderId`, `amount`, `currency`
    and `refundedAt` missing → `InvalidMessageError` naming it;
  - `amount` `"129.90"` / `true` / `-1` / `100` (valid) / large exact (valid);
  - `currency` `"eur"` / `"EURO"` / `123`;
  - naive `refundedAt`;
  - bad JSON. Gate.
- [ ] T008 [US1] Application with the typed retryable error (depends on T005, T006, T007):
  - create `src/notifications/application/errors.py` with
    `OrderContextNotFoundError(Exception)`: an `order_id` attribute, the message
    `no order context for order <order_id>`, and a docstring saying it is retryable because the
    context may arrive later;
  - in `src/notifications/application/record_payment_refunded.py`, add
    `RecordPaymentRefundedService(order_contexts, notifications, sender, clock, ids)` with
    `handle(command) -> RecordOutcome`: `get` → `None` raises `OrderContextNotFoundError`;
    otherwise `Notification.payment_refunded(customer_id=context.customer_id, …)` →
    `record_and_send`.

  Tests in `tests/unit/application/test_record_payment_refunded.py`:
  - context found → stored with the context's customer and the exact message, sent once;
  - context missing → `OrderContextNotFoundError` whose message names the order; nothing stored,
    nothing sent, no order context written;
  - duplicate → `ALREADY_RECORDED`, not sent;
  - a notification repository error propagates. Gate.
- [ ] T009 [US1] Topic registration (depends on T002, T008):
  - in `src/notifications/infrastructure/settings.py`, add `kafka_topic_payment_refunded`
    (default `payments.payment-refunded`, `min_length=1`, a description) and include it in
    `_distinct_topics`;
  - in `src/notifications/main.py`, create `RecordPaymentRefundedService` (sharing the order
    context and notification repositories, sender, clock and ids) and add the fourth `Route` in
    `build_routing_table` (new parameter `record_payment_refunded`).

  Tests:
  - `tests/unit/infrastructure/test_settings.py`: the default; an environment override; equal to
    an order topic → error naming `kafka_topic_payment_refunded`;
  - `tests/unit/test_main_routes.py`: the four topics, in order. Gate.
- [ ] T010 [US1] Component test in the new `tests/component/test_payment_refunded_flow.py`:
  - fixture: `create_consumed_topics` and `run_app(component_settings(...))`;
  - produce an `orders.order-placed` event, `eventually` its `order_context` exists, then produce
    a refund for the same `orderId` (a different `aggregateId`);
  - `eventually` exactly one `PAYMENT_REFUNDED` document, with `customerId` = the placement's
    customer, `orderId` = the event's `orderId`, the exact message, and `sourceEventId` = the
    refund's `eventId`;
  - one `Simulated email sent … type=PAYMENT_REFUNDED` record (`caplog`);
  - no notification has `orderId` equal to the payment id. Gate.

**Checkpoint**: MVP: refunds for known orders are recorded end to end.

---

## Phase 4: User Story 2 — A refund for an unknown order is never lost or misaddressed (Priority: P1)

**Goal**: A missing order context → retries with backoff → dead letter with
`OrderContextNotFoundError`; never a notification without a customer.

**Independent Test**: Produce a refund for an order with no context. With fast retries, one dead
letter with `errorType` `OrderContextNotFoundError`, `attempts` = `max_attempts` and the order in
the message; the offset committed; no notification.

- [ ] T011 [P] [US2] Unit test of the retry path with the real service, in
  `tests/unit/application/test_record_payment_refunded_retries.py`. Use a
  `RetryingMessageProcessor` (`RecordingSleeper`, `InMemoryDeadLetterStore`), a `RoutingTable`
  with the refund route (`parse_payment_refunded` → `RecordPaymentRefundedService.handle`), and
  in-memory repositories.
  1. **Never there**: no context ever → `HANDLED`, sleeps `[1, 2, 4, 8]`, one dead letter with
     `error_type == "OrderContextNotFoundError"`, `attempts == 5` and the order id in
     `error_message`, and no notification.
  2. **Arrives during the retries** (spec US2 scenario 2, where another instance records the
     context): first add to `tests/support/fakes.py` a `LateOrderContextRepository(context, *,
     missing_calls: int)`. Its `get` returns `None` for the first `missing_calls` calls and
     `context` afterwards; its `add_if_absent` raises `AssertionError`, because refunds must never
     write contexts. With `missing_calls=2` → `HANDLED`, notification recorded with that
     customer, sleeps `[1, 2]`, no dead letter.

  Production code is not expected to change. If it does, keep it inside this story's files.
  Gate.
- [ ] T012 [US2] Component test in `tests/component/test_payment_refunded_flow.py`, with a
  fast-retry fixture (`consumer_retry_max_attempts=3`, base 0.05 s, max 0.1 s):
  - produce a refund for an order that never had a placement;
  - `eventually` the group's committed offset passes it (`committed_offset`);
  - one dead letter at that topic, partition and offset, with `errorType`
    `OrderContextNotFoundError`, `attempts == 3`, and `errorMessage` containing the order id;
  - no notification for that `sourceEventId`;
  - WARNING `retrying … attempt=2/3` records captured (`caplog`). Gate.

**Checkpoint**: Refunds are never lost or misaddressed.

---

## Phase 5: User Story 3 — The refund topic is as reliable as the order topics (Priority: P2)

**Goal**: Redelivery and invalid messages behave as for the other topics.

**Independent Test**: The same refund twice → one notification; an invalid refund → a dead letter
with `attempts` 1, and the next refund is processed; one app instance handles all four topics for
one order.

- [ ] T013 [P] [US3] Integration test in `tests/integration/test_notification_repository.py`: a
  `Notification.payment_refunded` added twice with the same `source_event_id` → `True` then
  `False`, one document (after `ensure_indexes`); round trip `from_document(to_document(n)) ==
  n`. Gate.
- [ ] T014 [US3] Component tests in `tests/component/test_payment_refunded_flow.py`:
  1. **Redelivery**: placement → wait for the context → the same refund bytes twice (same key) →
     `eventually` both offsets committed → one notification, one send log record, no dead letter
     at those offsets.
  2. **Invalid**: with the fast-retry fixture, a refund without `currency`, then a valid refund
     for a placed order (same key) → one dead letter with `attempts == 1` and
     `currency: Field required`, and the valid refund recorded.
  3. **One consumer for all four topics** (spec US3 scenario 3): in one app instance, produce an
     `orders.order-placed`, wait for its context, then produce an `orders.order-confirmed`, an
     `orders.order-cancelled` and a `payments.payment-refunded` for that order → `eventually` four
     notifications (`ORDER_RECEIVED`, `ORDER_CONFIRMED`, `ORDER_CANCELLED`, `PAYMENT_REFUNDED`)
     for the same order and customer; readiness reports a single `kafka: CONNECTED`. Gate.

**Checkpoint**: All three stories pass independently and together.

---

## Phase 6: Polish & documentation

- [ ] T015 [P] Update `README.md`:
  - status line;
  - in the Consumed topics table, the `payments.payment-refunded` row becomes implemented
    (US-03), with the message and "customer from the `OrderContext` by `orderId`";
  - `KAFKA_TOPIC_PAYMENT_REFUNDED` in the Configuration table;
  - a **Known limitation: refund before its order context** subsection (FR-016). It explains:
    retries then a dead letter; why a backlog can cause it (one message at a time per instance);
    how to find such refunds (`inbound_dead_letters`, `errorType: "OrderContextNotFoundError"`);
    no replay. Gate.
- [ ] T016 [P] Update `CLAUDE.md`:
  - move the "Active feature" notes into permanent rules (`orderId` vs `aggregateId` for
    `payments.*`; customer only from `OrderContextRepository.get`; `OrderContextNotFoundError` is
    retryable; `EventEnvelope` / `OrderEventEnvelope`; test topics = `CONSUMED_TOPICS`);
  - extend "Where things are" with US-03;
  - remove "Active feature". Gate.
- [ ] T017 [P] In the ecommerce-infra repository, on branch
  `docs/notifications-consumes-payment-refunded` from an up-to-date `main`, update
  `docs/events.md` (research R9):
  - in the Topics table, the Notifications consumer of `payments.payment-refunded` becomes
    implemented;
  - in the `payments.payment-refunded` section, one sentence: Notifications resolves the customer
    through the order context recorded from `orders.order-placed`, and dead-letters a refund whose
    context is missing after retries.

  Commit it there. In this repository, the task commit only ticks the task. Gate.
- [ ] T018 Run quickstart.md steps 2–4 against ecommerce-infra (the service via `uv run` with
  `MONGODB_URI` on `127.0.0.2`), using a throwaway database and consumer group. Wait for the order
  context before sending the refund (step 2). Clean up afterwards. Record any deviation as a fix.
  Gate.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001–T002)** → **Foundational (T003–T005)** → **US1 (T006–T010)** →
  **US2 (T011–T012)** → **US3 (T013–T014)** → **Polish (T015–T018)**.
- Inside Foundational: T003, T004 and T005 are independent.
- **US1**: T006 and T007 are independent; T008 needs T005–T007; T009 needs T002 and T008; T010
  needs T009.
- **US2** needs US1 (the route and service exist); it adds proof, not production code.
- **US3** needs US1.

### Story completion order

```text
T001 ┐            T003 ┐
T002 ┴─► (setup) ─► T004 ┼─► US1: T006 ┐
                  T005 ┘        T007 ┴─► T008 ─► T009 ─► T010 ─┬─► US2: T011 ─► T012 ─┐
                                                              └─► US3: T013 ─► T014 ─┴─► Polish
```

### Within each story

domain → parser → application → topic registration → component test, each ending with the gate
and one commit.

## Parallel Opportunities

- **Setup**: T001 ∥ T002.
- **Foundational**: T003 (domain) ∥ T004 (messaging) ∥ T005 (port, fake, Mongo).
- **US1**: T006 (domain) ∥ T007 (parser and command).
- **US2 / US3**: T011 (unit) ∥ T013 (integration).
- **Polish**: T015 ∥ T016 ∥ T017.

### Parallel example: User Story 1

```text
Task: "T006 [P] [US1] payment_refunded_message + currency check + factory in src/notifications/domain/"
Task: "T007 [P] [US1] PaymentRefundedEvent + parse_payment_refunded in src/notifications/messaging/payment_refunded.py"
```

## Implementation Strategy

### MVP first

1. Setup + Foundational: the gate is green and nothing visible changes.
2. US1 (T006–T010): refunds for known orders are recorded. Validate with quickstart step 2.
3. US2 (T011–T012): refunds for unknown orders are retried and dead-lettered, never misaddressed.
   Validate with quickstart step 3. US1 + US2 is the shippable increment: the spec forbids
   shipping US1 without the missing-context guarantee.

### Incremental delivery

4. US3 proves redelivery and invalid handling (quickstart step 4).
5. Polish: README (with the known limitation), CLAUDE.md, the ecommerce-infra PR, a quickstart
   run. Then the PR "Implements US-03: Payment refunded notification".

Each of the 18 tasks is one commit with a green gate.
