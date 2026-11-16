---

description: "Task list for US-02 order confirmed and order cancelled notifications"
---

# Tasks: Order confirmed and order cancelled notifications (US-02)

**Input**: Design documents from `specs/002-order-confirmed-cancelled/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R8), data-model.md, contracts/, quickstart.md

**Tests**: Required (constitution Principles VI and VII; requested in the planning input).

**Gate (every task)**: each task ends with the full quality gate, in this order:
`uv run ruff check` → `uv run ruff format --check` → `uv run mypy` → `uv run pytest --cov`. A task
is done only when all four pass. Write the task's test first and see it fail, then implement,
then run the gate. **One task = one commit**; commit messages `feat(US-02): …`, `test(US-02): …`,
`refactor(US-02): …`, `docs(US-02): …`. Never commit a red gate.

**Order** (planning input): domain → parsers → application → routing registration →
integration/component tests → docs. The refactors that every story needs come first (Phase 2) in
the same layer order. Each user story phase then repeats the order for its own topic.

**Story labels**: `[US1]`–`[US3]` are the user stories of spec.md (US1 confirmed, US2 cancelled,
US3 reliability). They are inside the single backlog item US-02.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the spec user story the task belongs to

---

## Phase 1: Setup

**Purpose**: Test fixtures for the new topics. No production change.

- [X] T001 [P] Add `create_topics(bootstrap, names, partitions=3)` to `tests/support/kafka.py`.
  It loops over `create_topic`. Then add `ORDER_CONFIRMED_TOPIC = "orders.order-confirmed"` and
  `ORDER_CANCELLED_TOPIC = "orders.order-cancelled"` test constants, and
  `order_status_payload(**overrides)` (envelope + `customerId`, `MISSING` removes a key), to
  `tests/support/events.py`. No test uses them yet; the gate stays green. Gate.

---

## Phase 2: Foundational (behaviour-preserving refactors)

**Purpose**: The shared pieces all three topics use. `orders.order-placed` behaves exactly as
before; every existing US-01 test stays green (some are adapted to new names).

**⚠️ CRITICAL**: No user story work starts before this phase is complete.

- [X] T002 [P] Domain:
  - in `src/notifications/domain/notification.py`, add `ORDER_CONFIRMED` and `ORDER_CANCELLED`
    to `NotificationType`;
  - extract a private classmethod `_create(*, notification_id, customer_id, order_id, type,
    message, source_event_id, now)` that sets `EMAIL`, `SENT` and `require_utc_millis(now)`;
  - make `order_received` use `_create`.

  Tests in `tests/unit/domain/test_notification.py`: the enum values exist with these exact
  strings; `order_received` still produces the same notification. Gate.
- [X] T003 [P] Parsers: create `src/notifications/messaging/events.py` (research R2):
  - move `_json_string`, `_json_integer`, `_json_number` and `JsonUuid` from `order_placed.py`;
  - add `OrderEventEnvelope` (frozen, `extra="ignore"`, `use_attribute_docstrings=True`; fields
    `eventId`, `aggregateId`, `occurredAt`, `version`, `customerId`, each with a field docstring);
  - add `parse_event(message, model: type[E]) -> E`, the current `parse_order_placed` body made
    generic.

  Make `OrderPlacedEvent` extend `OrderEventEnvelope`, adding only `totalAmount`, and make
  `parse_order_placed` call `parse_event`. All tests in `tests/unit/messaging/test_order_placed.py`
  pass unchanged. Add `tests/unit/messaging/test_events.py`: the envelope rejects `version`
  `true`/`1.0`/`"1"`, a missing `customerId` and a numeric UUID; ignores extra fields; reports
  every invalid field. Gate.
- [X] T004 [P] Application: create `src/notifications/application/recording.py` with
  `RecordOutcome` (moved; re-exported from `record_order_received.py` so imports keep working)
  and `async record_and_send(notification, notifications, sender) -> RecordOutcome` (research R4):
  - `add`, then `send` if stored, then return `RECORDED`;
  - otherwise log INFO `Event already recorded: type=%s sourceEvent=%s order=%s` and return
    `ALREADY_RECORDED`.

  Make `RecordOrderReceivedService.handle` use it. Update the duplicate-log assertion in
  `tests/unit/application/test_record_order_received.py` to the new text. Add
  `tests/unit/application/test_recording.py`: stored → sent once; duplicate → not sent, INFO
  logged with the type; a repository error propagates and nothing is sent. Gate.
- [X] T005 [P] Routing table: create `src/notifications/messaging/routing.py` (research R1):
  - `Route[C]`: a frozen generic dataclass with `topic`,
    `parse: Callable[[ConsumedMessage], C]` and `handle: Callable[[C], Awaitable[object]]`;
  - an `Any`-free protocol `RunnableRoute` (`topic: str`, `async run(message) -> None`), which
    `Route[C]` implements as "parse, then handle";
  - `RoutingTable(routes: Sequence[RunnableRoute])`. It raises `ValueError` on a duplicate topic,
    exposes `topics: tuple[str, ...]`, and has `async handle(message)`, so it implements
    `MessageHandler`. `handle` raises `InvalidMessageError(f"no route for topic {topic}")` when
    unrouted, and otherwise runs the route once (parse once, service method once).
  - (Moved to T006 during implementation: removing `topic` from `MessageHandler` breaks the
    manager, which still reads `handler.topic` until T006, so it must happen together with the
    manager change to keep every commit green.)

  Tests in `tests/unit/messaging/test_routing.py`:
  - `handle` reaches the matching route only;
  - the parser output reaches the handler;
  - a parser `InvalidMessageError` propagates and the handler is not called;
  - an unrouted topic → `InvalidMessageError`;
  - a duplicate topic → `ValueError`;
  - `topics` is in registration order. Gate.
- [X] T006 Routing registration for the existing topic (depends on T003, T005):
  1. `KafkaConsumerManager` takes `routing: RoutingTable | None = None` instead of
     `handlers: Sequence[MessageHandler]`. It subscribes to `routing.topics`, and for each record
     calls `processor.process(message, routing)` (the table is the `MessageHandler`). It still
     requires a processor when routing is given.
  1a. In `src/notifications/messaging/messages.py`, remove the `topic` property from the
     `MessageHandler` protocol (keep only `async handle(message)`), so `RoutingTable` type-checks
     as a `MessageHandler` under mypy strict (moved here from T005).
  2. `parse_order_placed` returns `OrderPlacedCommand` (the parser output feeds the service
     method directly).
  3. Remove `OrderPlacedHandler` and `ORDER_PLACED_TOPIC`.
  4. In `src/notifications/main.py`, build
     `RoutingTable([Route("orders.order-placed", parse_order_placed, record_order_received.handle)])`.

  Adapt `tests/unit/messaging/test_consumer_manager_consume.py` (pass a `RoutingTable` with one
  route) and `tests/unit/messaging/test_order_placed.py` (handler tests become "the route calls
  the service once"). The component tests for `orders.order-placed` stay green. Gate.
- [X] T007 Settings (depends on T006). In `src/notifications/infrastructure/settings.py`, add:
  - `kafka_topic_order_placed`, default `orders.order-placed`;
  - `kafka_topic_order_confirmed`, default `orders.order-confirmed`;
  - `kafka_topic_order_cancelled`, default `orders.order-cancelled`;
  - `min_length=1` and a description on each;
  - a model validator requiring the three names to be distinct.

  `main.py` uses `settings.kafka_topic_order_placed` for the route. Tests in
  `tests/unit/infrastructure/test_settings.py`: defaults; reading `KAFKA_TOPIC_ORDER_CONFIRMED`
  from the environment; an empty value → error; two equal names → error naming the fields. Gate.

**Checkpoint**: The gate is green and behaviour is unchanged for `orders.order-placed`. The
routing table and the shared envelope are ready.

---

## Phase 3: User Story 1 — Order confirmed (Priority: P1) 🎯 MVP

**Goal**: `orders.order-confirmed` → one `ORDER_CONFIRMED` notification, then the send log line.

**Independent Test**: Produce one confirmation to a real Kafka. One notification exists with the
message `Your order <orderId> was confirmed.` and one send log line; no order context exists.

- [X] T008 [P] [US1] Domain: add `order_confirmed_message(order_id) -> str` (`Your order <id> was
  confirmed.`) to `src/notifications/domain/messages.py`, and
  `Notification.order_confirmed(*, notification_id, customer_id, order_id, source_event_id, now)`
  (via `_create`) to `src/notifications/domain/notification.py`.

  Tests in `tests/unit/domain/test_messages.py` (exact text; deterministic) and
  `tests/unit/domain/test_notification.py` (all fields; type `ORDER_CONFIRMED`; naive `now` →
  `NaiveTimestampError`). Gate.
- [X] T009 [P] [US1] Parser:
  - add `OrderConfirmedCommand` (frozen dataclass: `event_id`, `order_id`, `customer_id`) to the
    new `src/notifications/application/record_order_status.py` (commands only in this task);
  - create `src/notifications/messaging/order_status.py` with
    `OrderConfirmedEvent(OrderEventEnvelope)` (class docstring from `events.md`) and
    `parse_order_confirmed(message) -> OrderConfirmedCommand`.

  Tests in `tests/unit/messaging/test_order_status.py`:
  - valid → command with the order id from `aggregateId`;
  - an extra `reason` field is ignored;
  - each required field missing → `InvalidMessageError` naming it;
  - bad UUID;
  - bad JSON (`b"not json"`);
  - `version` 2. Gate.
- [X] T010 [US1] Application (depends on T008, T009): add
  `RecordOrderStatusService(notifications, sender, clock, ids)` with
  `record_confirmed(command) -> RecordOutcome` (factory → `record_and_send`) to
  `src/notifications/application/record_order_status.py`.

  Tests in `tests/unit/application/test_record_order_status.py`:
  - stored once and sent once, with the exact message;
  - a duplicate → `ALREADY_RECORDED`, not sent;
  - a repository error propagates, not sent. Gate.
- [X] T011 [US1] Routing registration (depends on T010): in `src/notifications/main.py`, create
  `RecordOrderStatusService` and add
  `Route(settings.kafka_topic_order_confirmed, parse_order_confirmed, record_order_status.record_confirmed)`.

  Unit test in `tests/unit/messaging/test_routing.py` or a new `tests/unit/test_main_routes.py`:
  the app's routing table built from default settings has exactly the topics
  `orders.order-placed` and `orders.order-confirmed`. Expose a small `build_routing_table(...)`
  function in `main.py` so it can be tested without starting the app. Gate.
- [X] T012 [US1] Component test in `tests/component/test_order_status_flow.py`:
  - a fixture creates the three order topics (T001 helper) and runs the app (`run_app`,
    `component_settings`);
  - produce one confirmation, then `eventually` exactly one `ORDER_CONFIRMED` document with the
    exact message, customer, order and `sourceEventId`;
  - one `Simulated email sent … type=ORDER_CONFIRMED` record (`caplog`);
  - no `order_context` document for that order (FR-009). Gate.

**Checkpoint**: Confirmations are recorded end to end.

---

## Phase 4: User Story 2 — Order cancelled, with or without a reason (Priority: P1)

**Goal**: `orders.order-cancelled` → one `ORDER_CANCELLED` notification whose message includes
the normalised reason, or the generic text.

**Independent Test**: Produce one cancellation with a reason and one without. Two notifications
exist with the exact two messages.

- [X] T013 [US2] Domain, in `src/notifications/domain/messages.py`:
  - `MAX_REASON_LENGTH = 500`;
  - `normalise_reason(raw: str | None) -> str | None`, following the data-model.md rules;
  - `order_cancelled_message(order_id, reason) -> str`.

  Add `Notification.order_cancelled(..., reason, ...)` to `src/notifications/domain/notification.py`.

  Tests in `tests/unit/domain/test_messages.py`, one per data-model.md table row:
  - absent / `None` / `""` / `"   "` → generic;
  - a plain reason;
  - surrounding spaces and a trailing period;
  - `"a\r\nb\tc"` → `a b c`;
  - exactly 500 characters kept;
  - 501 characters → 500 + `…`;
  - `"..."` → only one period removed;
  - deterministic.

  In `tests/unit/domain/test_notification.py`: the factory's type and message, with and without a
  reason. Gate.
- [X] T014 [P] [US2] Parser:
  - add `OrderCancelledCommand` (`event_id`, `order_id`, `customer_id`, `reason: str | None`) to
    `src/notifications/application/record_order_status.py`;
  - add `OrderCancelledEvent(OrderEventEnvelope)` with `reason: str | None = None`, a
    `BeforeValidator` accepting only `str` or `None` (`ValueError("must be a JSON string or
    null")`), and a field docstring, plus `parse_order_cancelled(message) ->
    OrderCancelledCommand`, to `src/notifications/messaging/order_status.py`.

  Tests in `tests/unit/messaging/test_order_status.py`:
  - a reason passes through raw (not normalised);
  - `reason` absent or `null` → `None`;
  - `reason` `42` / `true` / `{}` / `[]` → `InvalidMessageError` containing
    `reason: Value error, must be a JSON string or null`;
  - a missing `customerId`;
  - bad JSON. Gate.
- [X] T015 [US2] Application (depends on T013, T014): add `record_cancelled(command) ->
  RecordOutcome` to `RecordOrderStatusService` in
  `src/notifications/application/record_order_status.py`.

  Tests in `tests/unit/application/test_record_order_status.py`:
  - with a reason → message with the reason, sent once;
  - without a reason → generic message;
  - a duplicate → not sent;
  - a repository error propagates. Gate.
- [X] T016 [US2] Routing registration (depends on T015): in `src/notifications/main.py`
  (`build_routing_table`), add
  `Route(settings.kafka_topic_order_cancelled, parse_order_cancelled, record_order_status.record_cancelled)`.
  Update the routing-table test: exactly the three order topics. Gate.
- [X] T017 [US2] Component tests in `tests/component/test_order_status_flow.py`: produce a
  cancellation with the reason `payment failed: amount exceeds the threshold` and one without a
  reason; `eventually` two `ORDER_CANCELLED` documents with the exact messages
  `Your order <id> was cancelled: payment failed: amount exceeds the threshold.` and
  `Your order <id> was cancelled.`, each with one send log record. Gate.

**Checkpoint**: Both new topics are recorded end to end.

---

## Phase 5: User Story 3 — The new topics are as reliable as the first one (Priority: P2)

**Goal**: Prove that the US-01 guarantees hold for the new topics through the one consumer.

**Independent Test**: Redelivery → one notification; invalid → dead letter and the next message
processed; one consumer for all three topics.

- [X] T018 [P] [US3] Integration test in `tests/integration/test_notification_repository.py`:
  - for a confirmed and a cancelled notification, `add` twice with the same `source_event_id` →
    `True` then `False`, one document each (unique index, after `ensure_indexes`);
  - `from_document(to_document(n)) == n` for both new types. Gate.
- [X] T019 [P] [US3] Component test in `tests/component/test_order_status_flow.py`: produce the
  same confirmation bytes twice (same key); `eventually` the group's committed offset passes the
  second delivery (`committed_offset`); exactly one notification; one send log record; no dead
  letter at those offsets. Gate.
- [X] T020 [US3] Component test in `tests/component/test_order_status_flow.py`, with fast retry
  settings as in US-01's dead-letter test:
  - produce, with the same key, an invalid cancellation (`reason: 42`), then a cancellation
    without `customerId`, then a valid cancellation;
  - `eventually` all three offsets committed;
  - two dead letters with `attempts == 1`, `topic == "orders.order-cancelled"` and the expected
    reasons;
  - one notification for the valid one. Gate.
- [ ] T021 [US3] Component test in `tests/component/test_order_status_flow.py`, independence and
  one consumer:
  - produce a confirmation and a cancellation for an order whose placement was never produced →
    both notifications recorded;
  - also produce one `orders.order-placed` event → recorded by the same app instance;
  - readiness reports a single `kafka: CONNECTED`. Gate.

**Checkpoint**: All three stories pass independently and together.

---

## Phase 6: Polish & documentation

- [ ] T022 [P] Update `README.md`:
  - Consumed topics table: `orders.order-confirmed` and `orders.order-cancelled` → implemented
    (US-02), with their message templates;
  - add a short "routing table" sentence to Processing guarantees;
  - add the three `KAFKA_TOPIC_ORDER_*` variables to the Configuration table;
  - update the status line. Gate.
- [ ] T023 [P] Update `CLAUDE.md`:
  - move the "Active feature" notes into permanent rules: routing table and registration, shared
    `messaging/events.py`, domain reason normalisation, topic settings, generic duplicate log;
  - extend "Where things are" with US-02;
  - remove "Active feature". Gate.
- [ ] T024 [P] In the ecommerce-infra repository, on branch
  `docs/notifications-consumes-order-status`, update `docs/events.md` (research R8):
  - in the Topics table, the Notifications consumer of `orders.order-confirmed` and
    `orders.order-cancelled` becomes implemented;
  - in the `orders.order-cancelled` section, add one sentence: Notifications shows `reason` in the
    message when present.

  Do not change the payload. Gate (this repository's gate is unaffected; run it anyway for the
  rule).
- [ ] T025 Record the Principle VII follow-up (plan.md Complexity Tracking) in `CLAUDE.md`,
  under "Tests": US-04 MUST extend `tests/component/test_order_placed_flow.py` and
  `tests/component/test_order_status_flow.py` so every recorded notification is also read back
  through `GET /notifications/{id}` (constitution VII, "readable over HTTP"). Until then,
  component tests assert on MongoDB. Gate.
- [ ] T026 Run quickstart.md steps 2–5 against ecommerce-infra (the service via `uv run` with
  `MONGODB_URI` on `127.0.0.2`, or the Docker image on `ecommerce_default`). Use a throwaway
  database and consumer group, and clean them up afterwards. Record any deviation as a fix
  before the PR. Gate.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (T001)** → **Foundational (T002–T007)** → **US1 (T008–T012)** → **US2 (T013–T017)** →
  **US3 (T018–T021)** → **Polish (T022–T026)**.
- Inside Foundational: T002, T003, T004 and T005 are independent; T006 needs T003 and T005; T007
  needs T006.
- **US1** needs Foundational.
- **US2** needs Foundational. It also uses T009's `order_status.py` and `record_order_status.py`
  files and T011's `build_routing_table`, so after US1 it is a sequential follow-up.
- **US3** needs US1 and US2 (it tests both topics).

### Story completion order

```text
T001 ─► T002 ┐
        T003 ┼─► T006 ─► T007 ─► US1 (T008/T009 ─► T010 ─► T011 ─► T012)
        T004 │                     └─► US2 (T013/T014 ─► T015 ─► T016 ─► T017)
        T005 ┘                                └─► US3 (T018/T019 ─► T020 ─► T021) ─► Polish
```

### Within each story

domain → parser → application → routing registration → component test, each ending with the
gate and one commit.

## Parallel Opportunities

- **Foundational**: T002 (domain), T003 (messaging/events), T004 (application/recording) and
  T005 (messaging/routing) touch different files.
- **US1**: T008 (domain) ∥ T009 (parser + commands).
- **US2**: T014 (parser) can start in parallel with T013 (domain). They touch different files;
  T014 only adds to `order_status.py` and `record_order_status.py`.
- **US3**: T018 (integration) ∥ T019 (component redelivery).
- **Polish**: T022 ∥ T023 ∥ T024; T025 after T023 (both edit `CLAUDE.md`); T026 last.

### Parallel example: Foundational

```text
Task: "T002 [P] NotificationType values + _create in src/notifications/domain/notification.py"
Task: "T003 [P] OrderEventEnvelope + parse_event in src/notifications/messaging/events.py"
Task: "T004 [P] record_and_send in src/notifications/application/recording.py"
Task: "T005 [P] Route + RoutingTable in src/notifications/messaging/routing.py"
```

## Implementation Strategy

### MVP first

1. Setup and Foundational: the gate is green, `orders.order-placed` is unchanged, and the
   routing table is in place.
2. US1 (T008–T012): confirmations are recorded. Validate with quickstart step 2.
3. US2 (T013–T017): cancellations with and without a reason. Validate with quickstart step 3.
   US1 + US2 is the shippable increment.

### Incremental delivery

4. US3 proves redelivery, dead letters and the one-consumer behaviour (quickstart steps 4–5).
5. Polish: README, CLAUDE.md, the ecommerce-infra PR, a quickstart run. Then the PR
   "Implements US-02: Order confirmed and order cancelled notifications".

Each of the 26 tasks is one commit with a green gate.
