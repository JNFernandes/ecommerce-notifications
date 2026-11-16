---

description: "Task list for US-01 order received notification"
---

# Tasks: Order received notification (US-01)

**Input**: Design documents from `specs/001-order-received-notification/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Required. Constitution Principles VI and VII apply:
- every domain and application method needs a happy-path test and an error/edge test;
- repositories and unique indexes need integration tests against real MongoDB;
- the consume → store flow needs component tests with real MongoDB and Kafka.

**Gate rule (Principle X)**: after **every** task, run `uv run ruff check`,
`uv run ruff format --check`, `uv run mypy` and `uv run pytest --cov`, and fix any failure before
the next task. Within a task, write the test first and see it fail locally. Commit the test
together with the implementation that makes it pass; never commit a red gate. Commit messages:
`feat(US-01): …`, `test(US-01): …`, `docs(US-01): …`.

**Organization**: Tasks are grouped by the five user stories of spec.md. In this file, `[US1]`…`[US5]`
are the spec's **user stories** inside the single backlog item US-01; they are not separate
backlog items.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the spec user story the task belongs to (US1–US5)

## Path Conventions

Single project, src layout: `src/notifications/…`, `tests/…` at the repository root (see plan.md
"Project Structure").

---

## Phase 1: Setup (shared test infrastructure)

**Purpose**: Test scaffolding that several stories need. No production behaviour changes.

- [X] T001 [P] Create the integration test package `tests/integration/__init__.py` (module
  docstring: "Integration tests: repositories and indexes against a real MongoDB
  (Testcontainers)") and `tests/integration/conftest.py`:
  - a session-scoped `mongodb_url` fixture starting `MongoDbContainer("mongo:7")` from
    `testcontainers.community.mongodb`;
  - a function-scoped `database` fixture that yields an `AsyncDatabase[dict[str, object]]` on a
    unique database name per test (e.g. `f"it_{uuid4().hex}"`) and drops it afterwards.

  Move the session-scoped `mongodb_url` fixture from `tests/component/conftest.py` to
  `tests/conftest.py` so both packages share one container.
- [X] T002 [P] Create `tests/support/events.py` with `order_placed_payload(**overrides) -> dict[str, object]`:
  - it returns a valid `orders.order-placed` JSON object (fresh UUIDs for `eventId`,
    `aggregateId` and `customerId`; `occurredAt` now in UTC ISO 8601; `version` 1; one item;
    `totalAmount` `Decimal("129.90")`);
  - an override value of the sentinel `MISSING` removes the key.

  Also add `to_json_bytes(payload) -> bytes`, which serialises `Decimal` as a JSON number
  (e.g. `json.dumps(..., default=...)` that emits the decimal's string as a raw number via a
  custom encoder) and can also produce invalid bytes for tests.
- [X] T003 [P] Create `tests/support/kafka.py` and add Kafka fixtures to `tests/component/conftest.py`:
  - a session-scoped `kafka_container` with `KafkaContainer("confluentinc/cp-kafka:7.6.1")`;
  - `kafka_bootstrap` (its bootstrap server string);
  - `async create_topic(bootstrap, name, partitions=3)` using `AIOKafkaAdminClient`, which
    ignores "topic already exists";
  - `async produce(bootstrap, topic, value: bytes, key: bytes | None = None)` using
    `AIOKafkaProducer` (start, `send_and_wait`, stop);
  - `pause_kafka(container)` / `unpause_kafka(container)` via `container.get_wrapped_container().pause()/unpause()`.

  Type the aiokafka objects at this test boundary; any `Any` gets a justification comment.

**Checkpoint**: gate green; no behaviour change.

---

## Phase 2: Foundational (blocking prerequisites)

**Purpose**: Domain basics, ports and the consume loop that every story builds on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 [P] Create `src/notifications/domain/errors.py`:
  - `DomainError(Exception)`;
  - `InvalidOrderTotalError(DomainError)`, raised for a negative or non-finite total;
  - `NaiveTimestampError(DomainError)`, raised when a timestamp has no timezone.

  Each class has a docstring.
- [X] T005 [P] Create `src/notifications/domain/time.py` with `require_utc_millis(value: datetime) -> datetime`:
  - it raises `NaiveTimestampError` if `value.tzinfo is None`;
  - otherwise it returns the value converted to UTC and truncated to milliseconds
    (`microsecond // 1000 * 1000`).

  Tests in `tests/unit/domain/__init__.py` (docstring) and `tests/unit/domain/test_time.py`:
  - truncation;
  - conversion from a +02:00 offset;
  - naive input → error.
- [X] T006 [P] Create `src/notifications/application/ports.py` with the `typing.Protocol`s from
  data-model.md:
  - `Clock.now() -> datetime`;
  - `IdGenerator.new_id() -> UUID`;
  - `NotificationSender.send(notification: Notification) -> None`;
  - `NotificationRepository.add(notification) -> bool` (async);
  - `OrderContextRepository.add_if_absent(context) -> OrderContext | None` (async).

  Add each Protocol only when its domain type exists. In this task add `Clock` and `IdGenerator`;
  T013 and T024 add the others.
- [X] T007 [P] Create `src/notifications/infrastructure/system.py`:
  - `SystemClock.now()` returns `datetime.now(UTC)`;
  - `Uuid4Generator.new_id()` returns `uuid4()`.

  Tests in `tests/unit/infrastructure/test_system.py`: the result is aware UTC; ids are UUID4 and
  differ.
- [X] T008 Create `src/notifications/messaging/messages.py`:
  - `ConsumedMessage`: frozen dataclass with `topic`, `partition`, `offset`, `key: bytes | None`,
    `value: bytes | None`, `timestamp_ms: int`;
  - `InvalidMessageError(Exception)`, with a `reason: str` attribute;
  - `decode_json_object(value: bytes | None) -> dict[str, object]`. It decodes strict UTF-8 and
    runs `json.loads(text, parse_float=Decimal)`. It raises `InvalidMessageError` for `None`, a
    decode error, invalid JSON or a non-object result (research R1).

  Tests in `tests/unit/messaging/test_messages.py`:
  - a valid object; `Decimal` preserved for `123456789012345678.123456789`;
  - an int stays an int;
  - `None`; invalid UTF-8 (`b"\xff"`); `"not json"`; `"[]"`; `"null"`.
- [X] T009 Change `src/notifications/messaging/consumer_manager.py` to consume (research R4, R9):
  1. Add a `MessageHandler` Protocol with `topic: str` and
     `async handle(message: ConsumedMessage) -> None`.
  2. Add a `ProcessResult` `StrEnum` (`HANDLED`, `STOPPED`) and a `MessageProcessor` Protocol with
     `async process(message, handler) -> ProcessResult`.
  3. The constructor takes `handlers: Sequence[MessageHandler]` and `processor: MessageProcessor`;
     the consumer subscribes to the handlers' topics.
  4. While `CONNECTED`, loop on `getmany(timeout_ms=500, max_records=1)`. Convert each record to
     `ConsumedMessage`, call `processor.process(message, handlers_by_topic[record.topic])`, and on
     `HANDLED` commit `{TopicPartition(topic, partition): offset + 1}`. On `STOPPED`, return
     without committing.
  5. Wrap the commit in `try/except KafkaError`: log a WARNING
     (contracts/observability.md "Commit failed") and continue.
  6. Keep the periodic metadata check every `check_interval_seconds`, interleaved with polling.
  7. With no handlers, keep today's connect-and-watch behaviour.

  Update the existing tests in `tests/unit/messaging/test_consumer_manager.py` to the new
  constructor; they must stay green.
- [X] T010 Create `src/notifications/messaging/processor.py` with `HoldingMessageProcessor`, the
  interim safe default until US4:
  - it calls `handler.handle(message)` and returns `HANDLED`;
  - on any `Exception`, it logs an ERROR with topic, partition and offset, waits `hold_delay`
    through an injected `Sleeper`, and tries again;
  - it returns `STOPPED` when the sleeper reports a stop.

  Also create `src/notifications/messaging/retry.py`:
  - a `Sleeper` Protocol: `async sleep(seconds) -> bool`, which returns `True` if a stop was
    requested;
  - `StopAwareSleeper(stop_event: asyncio.Event)`.

  Tests:
  - `tests/support/fakes.py`: add a `RecordingSleeper` fake (records delays, never waits, can be
    told to report a stop after N calls);
  - `tests/unit/messaging/test_processor.py`: success → `HANDLED`; failure twice then success →
    `HANDLED` with two sleeps; stop during the hold → `STOPPED` and the handler is not called
    again.

  This guarantees from now on that a failing message is never skipped (FR-012, FR-018).

**Checkpoint**: gate green; the manager can consume and commit with the processor contract.
User stories can start.

---

## Phase 3: User Story 1 — Customer is told their order was received (Priority: P1) 🎯 MVP

**Goal**: A valid `orders.order-placed` event stores one `ORDER_RECEIVED` notification and logs the
simulated send. Then the offset is committed.

**Independent Test**: Produce one valid event to a real Kafka. Exactly one notification exists for
the customer and order, with the expected message, and one `Simulated email sent` log line was
written after the insert.

### Tests and implementation for User Story 1

- [X] T011 [P] [US1] Create `src/notifications/domain/messages.py` with
  `format_amount(amount: Decimal) -> str` and `order_received_message(order_id: UUID, total_amount: Decimal) -> str`
  (template `Your order <orderId> for <amount> was received.`):
  - `format_amount` quantizes to `0.01` with `ROUND_HALF_UP` and uses `format(q, "f")`;
  - it raises `InvalidOrderTotalError` for a negative or non-finite amount.

  Tests in `tests/unit/domain/test_messages.py`: `129.9`→`129.90`, `0`→`0.00`, `10.005`→`10.01`,
  `Decimal("1E+2")`→`100.00`, `123456789012345678.123456789`→`123456789012345678.12`, negative →
  error, `NaN` → error. Same input twice → same text.
- [X] T012 [P] [US1] Create `src/notifications/domain/notification.py`:
  - `NotificationType(StrEnum)` with `ORDER_RECEIVED`;
  - `NotificationChannel(StrEnum)` with `EMAIL = "email"`;
  - `NotificationStatus(StrEnum)` with `SENT`;
  - a `Notification` frozen, slotted dataclass with the fields of data-model.md;
  - the classmethod `Notification.order_received(*, notification_id, customer_id, order_id,
    total_amount, source_event_id, now)`. It uses `order_received_message` and
    `require_utc_millis(now)`, with channel `EMAIL` and status `SENT`.

  Tests in `tests/unit/domain/test_notification.py`:
  - all fields set as expected;
  - `created_at` truncated to milliseconds and in UTC;
  - naive `now` → `NaiveTimestampError`;
  - negative total → `InvalidOrderTotalError`;
  - immutability (assigning a field raises `FrozenInstanceError`).

  Update `src/notifications/domain/__init__.py` only if re-exports are useful; keep its docstring.
- [X] T013 [US1] Add `NotificationSender` and `NotificationRepository` (async
  `add(notification) -> bool`, documented: `False` = a notification with the same
  `source_event_id` already exists) to `src/notifications/application/ports.py`.
- [X] T014 [US1] Create `src/notifications/application/record_order_received.py`:
  - `OrderPlacedCommand` frozen dataclass (`event_id`, `order_id`, `customer_id`,
    `total_amount`);
  - `RecordOutcome(StrEnum)` with `RECORDED` and `ALREADY_RECORDED`;
  - `RecordOrderReceivedService(notifications, sender, clock, ids)` with
    `async handle(command) -> RecordOutcome`. It builds the notification through the domain
    factory, calls `notifications.add`, calls `sender.send(notification)` and returns `RECORDED`
    when `add` returns `True`.

  The `False` branch is implemented and tested in US2 (T020). Until then, `False` returns
  `ALREADY_RECORDED` without sending, and that branch is covered by T020.

  Fakes in `tests/support/fakes.py`: `InMemoryNotificationRepository` (dict by
  `source_event_id`; `add` returns `False` on a duplicate; can be set to raise), `RecordingSender`,
  `FixedClock`, `SequentialIdGenerator`.

  Tests in `tests/unit/application/test_record_order_received.py`:
  - stores one notification with the expected fields and sends it once;
  - a repository error propagates and nothing is sent;
  - an invalid total → `InvalidOrderTotalError`, nothing stored, nothing sent.
- [X] T015 [P] [US1] Create `src/notifications/infrastructure/sender.py` with
  `LoggingNotificationSender`. It logs exactly the INFO line of contracts/observability.md on the
  logger `notifications.send`.

  Test in `tests/unit/infrastructure/test_sender.py` with `caplog`: the logger name, the level
  and every `key=value` field.
- [X] T016 [US1] Create the package `src/notifications/infrastructure/repositories/__init__.py`
  (module docstring: MongoDB adapters for the application ports; documents mapped explicitly).

  Create `src/notifications/infrastructure/repositories/notifications.py`:
  - `MongoNotificationRepository(database)` with `add(notification) -> bool`, which runs
    `insert_one(to_document(notification))` and returns `True`;
  - the module functions `to_document` and `from_document`. UUIDs are stored as strings, the
    enums as their values, `createdAt` as an aware datetime; the field names follow data-model.md.

  Duplicate handling is added in T021.

  Integration test in `tests/integration/test_notification_repository.py`: an `add` then a raw
  `find_one` match the expected document; `from_document(to_document(n)) == n`, with `createdAt`
  aware UTC.
- [X] T017 [US1] Create `src/notifications/messaging/order_placed.py`:
  - `OrderPlacedEvent`: Pydantic v2, `frozen=True`, `extra="ignore"`, aliases in camelCase, and a
    docstring on every field taken from `ecommerce-infra/docs/events.md`;
  - JSON-type guards as `BeforeValidator`s (research R1): `version` must be a JSON integer `1`
    (reject `bool`, `Decimal`, `str`); `totalAmount` accepts `Decimal` or `int` (not `bool`),
    converted to `Decimal`, finite, `>= 0`; `occurredAt` must be a `str`; the UUIDs must be `str`;
  - `to_command() -> OrderPlacedCommand`;
  - `parse_order_placed(message: ConsumedMessage) -> OrderPlacedEvent`, which uses
    `decode_json_object` and maps a `ValidationError` to `InvalidMessageError`. The reason lists
    `field: message` for each error, e.g. `customerId: Field required`;
  - `OrderPlacedHandler(service: RecordOrderReceivedService)` with
    `topic = "orders.order-placed"` and `handle(message)`, which parses, calls
    `service.handle(event.to_command())` once and logs the duplicate INFO line when the outcome
    is `ALREADY_RECORDED`.

  Tests in `tests/unit/messaging/test_order_placed.py`, the full table from plan.md "Messaging
  (unit)":
  - valid, with and without `items` and extra fields;
  - each required field missing;
  - each UUID invalid;
  - `version` `2` / `true` / `1.0` / `"1"`;
  - `totalAmount` `"129.90"` / `true` / `-1` / `NaN` / `100` (valid, int) /
    `123456789012345678.123456789` (exact);
  - `occurredAt` naive or numeric;
  - the handler calls the service exactly once with the mapped command.
- [X] T018 [US1] Wire it in `src/notifications/main.py`: in the lifespan, build `SystemClock`,
  `Uuid4Generator`, `MongoNotificationRepository(db)`, `LoggingNotificationSender()`,
  `RecordOrderReceivedService`, `OrderPlacedHandler`, and a `HoldingMessageProcessor` with a
  `StopAwareSleeper`. Pass `handlers=[OrderPlacedHandler]` and the processor to
  `KafkaConsumerManager`. Keep the existing order: indexes → manager start → health service.
- [X] T019 [US1] Component test in `tests/component/test_order_placed_flow.py`, with the real
  lifespan via `app.router.lifespan_context(app)`, Mongo + Kafka containers, and the topic created
  with T003 helpers:
  - produce one valid event;
  - `wait_until` exactly one document in `notifications` with `orderId == aggregateId`, type
    `ORDER_RECEIVED`, channel `email`, status `SENT` and message
    `Your order <aggregateId> for 129.90 was received.`;
  - assert that one `Simulated email sent` record with that notification id was captured
    (`caplog`);
  - readiness reports `kafka: CONNECTED`.

  Use a fresh database name and consumer group per test.

**Checkpoint**: MVP. A placed order yields a stored notification and a simulated send. Verify
with quickstart.md step 3 (the order context part arrives with US3).

---

## Phase 4: User Story 2 — The same event never produces a second notification (Priority: P1)

**Goal**: Redelivery of an event stores nothing new, sends nothing, and is not an error.

**Independent Test**: Produce the same event twice. One notification, one send log line, no dead
letter; both messages committed.

- [X] T020 [US2] Add the duplicate tests in `tests/unit/application/test_record_order_received.py`:
  - when `notifications.add` returns `False`, `handle` returns `ALREADY_RECORDED` and the sender
    is not called;
  - handling the same command twice stores one notification, sends once, and the outcomes are
    `RECORDED` then `ALREADY_RECORDED`.

  Adjust `src/notifications/application/record_order_received.py` if needed.
- [X] T021 [US2] In `src/notifications/infrastructure/indexes.py`:
  - set `INDEX_SCHEMA_VERSION = 2`;
  - declare `uq_notifications_source_event_id` (`sourceEventId` asc, unique) and
    `ix_notifications_customer_created` (`customerId` asc, `createdAt` desc) in `INDEXES`, with
    collection-name constants.

  In `src/notifications/infrastructure/repositories/notifications.py`, catch `DuplicateKeyError`
  in `add` and return `False` **only** when `exc.details["keyPattern"] == {"sourceEventId": 1}`;
  otherwise re-raise.

  Integration tests:
  - `tests/integration/test_indexes.py`: `ensure_indexes` creates both indexes with the right
    keys and uniqueness; running it twice is a no-op;
  - `tests/integration/test_notification_repository.py`: a second `add` with the same
    `source_event_id` → `False` and still one document; a duplicate `_id` with a different
    `source_event_id` → `DuplicateKeyError` propagates.
- [X] T022 [US2] Component test in `tests/component/test_order_placed_flow.py`:
  - produce the same event bytes twice;
  - `wait_until` the committed offset of the group reaches 2 for that partition (use
    `AIOKafkaConsumer.committed` or admin `list_consumer_group_offsets`);
  - assert one notification, one `Simulated email sent` line, and an empty
    `inbound_dead_letters`.

**Checkpoint**: US1 + US2 make the MVP correct under at-least-once delivery.

---

## Phase 5: User Story 3 — The order's customer is remembered for later notifications (Priority: P2)

**Goal**: Each valid event stores an order context (first write wins), written before the
notification.

**Independent Test**: Process a valid event; an `order_context` document with `_id` = order id,
customer, exact total and a UTC `recordedAt` exists.

- [X] T023 [P] [US3] Create `src/notifications/domain/order_context.py`:
  - an `OrderContext` frozen, slotted dataclass (`order_id`, `customer_id`, `total_amount`,
    `recorded_at`);
  - `from_order_placed(*, order_id, customer_id, total_amount, now)`, which validates the total
    is finite and `>= 0` (`InvalidOrderTotalError`) and applies `require_utc_millis(now)`;
  - `conflicts_with(other) -> bool`: customer or total differ (`Decimal` comparison, so `129.9`
    equals `129.90`); `recorded_at` is ignored.

  Tests in `tests/unit/domain/test_order_context.py`:
  - creation;
  - negative total;
  - naive time;
  - `conflicts_with` for same values, a different customer, a different total, the equal totals
    `129.9` vs `129.90`, and a different `recorded_at` only.
- [X] T024 [US3] Add `OrderContextRepository` (async
  `add_if_absent(context) -> OrderContext | None`) to `src/notifications/application/ports.py`.

  Extend `RecordOrderReceivedService` (constructor gets `order_contexts`) so `handle` first builds
  the context and calls `add_if_absent`. If an existing context `conflicts_with` the new one, it
  logs the WARNING of contracts/observability.md. Then it continues with the notification
  (FR-004, FR-007).

  Fake `InMemoryOrderContextRepository` in `tests/support/fakes.py`; it records the call order in
  a shared journal with the notification fake.

  Tests in `tests/unit/application/test_record_order_received.py`:
  - the context is stored with the exact total;
  - the context is written before the notification (journal);
  - an existing equal context → no warning, still `RECORDED`;
  - an existing conflicting context is kept, a warning is logged (`caplog`), and the notification
    is still recorded;
  - a context repository error propagates, with no notification and no send.
- [X] T025 [US3] Create `src/notifications/infrastructure/repositories/order_contexts.py` with
  `MongoOrderContextRepository(database)` (research R3):
  - `add_if_absent` runs `find_one_and_update({"_id": str(order_id)}, {"$setOnInsert": {...}},
    upsert=True, return_document=ReturnDocument.BEFORE)` and returns `None` or the mapped
    existing context;
  - on a `DuplicateKeyError` (upsert race), retry once;
  - `totalAmount` is stored as `Decimal128(str(amount))` and read with `.to_decimal()`.

  Integration tests in `tests/integration/test_order_context_repository.py`:
  - first call → `None` and the document matches data-model.md;
  - second call with other values → returns the first context, and the document is unchanged
    (`recordedAt` included);
  - exact round trip of `123456789012345678.123456789`;
  - two concurrent `add_if_absent` calls for one order (`asyncio.gather`) → one document, and
    exactly one call returns `None`.
- [X] T026 [US3] Wire `MongoOrderContextRepository` into the service in `src/notifications/main.py`.

  Extend `tests/component/test_order_placed_flow.py` (happy-path test): assert the
  `order_context` document (`_id`, `customerId`, `totalAmount == Decimal128("129.90")`, aware
  `recordedAt`) and that its `recordedAt` is not after the notification's `createdAt`.

**Checkpoint**: The order context is available for US-03 (refunds); US1 and US2 still pass.

---

## Phase 6: User Story 4 — Messages that cannot be processed are set aside without blocking others (Priority: P2)

**Goal**: Invalid messages are dead-lettered at once; failing messages are retried with capped
exponential backoff and then dead-lettered; a failing dead-letter store holds the message.

**Independent Test**: An invalid message followed by a valid one → one dead letter
(`attempts: 1`) and one notification. A message whose storage fails → `max_attempts` attempts,
then a dead letter, and the next message is processed.

- [X] T027 [P] [US4] Add to `src/notifications/infrastructure/settings.py`:
  - `consumer_retry_max_attempts: int = 5` (`ge=1`);
  - `consumer_retry_base_delay_seconds: float = 1.0` (`gt=0`);
  - `consumer_retry_multiplier: float = 2.0` (`ge=1`);
  - `consumer_retry_max_delay_seconds: float = 10.0`;
  - a `model_validator` requiring max delay ≥ base delay.

  Each field has a description (contracts/configuration.md).

  Tests in `tests/unit/infrastructure/test_settings.py`: the defaults; reading each variable from
  the environment; each invalid value (0 attempts, 0 base delay, multiplier 0.5, max < base) →
  `ValidationError` naming the field.
- [X] T028 [P] [US4] Add `RetryPolicy` to `src/notifications/messaging/retry.py`: a frozen
  dataclass with `max_attempts`, `base_delay`, `multiplier`, `max_delay`,
  `delay_before_attempt(n: int) -> float` (= `min(base × multiplier^(n-2), max_delay)` for
  `n >= 2`; `ValueError` for `n < 2`) and the classmethod `from_settings(settings)`.

  Tests in `tests/unit/messaging/test_retry.py`:
  - delays 1, 2, 4, 8 with the defaults;
  - the cap with `max_delay=3` → 1, 2, 3, 3;
  - `multiplier=1` → constant;
  - `n=1` → `ValueError`.
- [X] T029 [P] [US4] Create `src/notifications/messaging/dead_letters.py`:
  - `DeadLetter`, a frozen dataclass with the fields of data-model.md;
  - a `DeadLetterStore` Protocol (async `add(dead_letter) -> bool`; `False` = already stored);
  - `dead_letter_from(message, *, error, attempts, first_failed_at, now) -> DeadLetter`, which
    encodes the key and payload as UTF-8 text, or base64 with `*_encoding="base64"` when they are
    not valid UTF-8, keeps `None` as `None`, and sets `error_type` to the class name and
    `error_message` to `InvalidMessageError.reason` or `str(error)`.

  Tests in `tests/unit/messaging/test_dead_letters.py`: a UTF-8 payload; `b"\xff\xfe"` → base64;
  a `None` key and value; the error fields for an invalid message and for a generic exception.
- [X] T030 [US4] Create `src/notifications/infrastructure/repositories/dead_letters.py` with
  `MongoDeadLetterStore(database)`: `add` inserts the document of data-model.md and returns
  `False` on a `DuplicateKeyError` whose `keyPattern` is the topic/partition/offset index;
  otherwise it re-raises.

  In `src/notifications/infrastructure/indexes.py`, add
  `uq_dead_letters_topic_partition_offset` (`topic`, `partition`, `offset` asc, unique). The
  version stays 2: it is unreleased and the same change set as T021.

  Integration tests in `tests/integration/test_dead_letter_store.py`: round trip of all fields; a
  second `add` for the same topic/partition/offset → `False`, one document; a base64 payload is
  stored as is.

  Extend `tests/integration/test_indexes.py` with the third index.
- [X] T031 [US4] Replace `HoldingMessageProcessor` with `RetryingMessageProcessor(policy, store,
  sleeper, clock)` in `src/notifications/messaging/processor.py`, following plan.md "Processing
  flow":
  - `InvalidMessageError` → dead letter with `attempts=1`, no retries;
  - any other `Exception` → WARNING (observability "Attempt failed"), then
    `sleeper.sleep(policy.delay_before_attempt(n))` and retry, up to `max_attempts`; after that,
    dead letter with `attempts=max_attempts` and the first failure time, plus an ERROR with the
    traceback;
  - dead letter stored, or `add` returned `False` → `HANDLED` (log INFO "already dead-lettered"
    when `False`);
  - the dead-letter store raises → ERROR "holding message", then `sleeper.sleep(policy.max_delay)`
    and restart the whole message from attempt 1 (Q1);
  - any sleep that reports a stop → `STOPPED`.

  The single `except Exception` carries a comment citing Principle V (handled: retried,
  dead-lettered, logged). Remove `HoldingMessageProcessor` and its tests, which are replaced.

  Fakes: `InMemoryDeadLetterStore` (can fail N times), a handler fake failing N times.

  Tests in `tests/unit/messaging/test_processor.py`:
  - success on the first try;
  - failing twice then succeeding → `HANDLED`, sleeps `[1, 2]`, no dead letter;
  - always failing → 5 attempts, sleeps `[1, 2, 4, 8]`, one dead letter with `attempts=5` and the
    last error, `first_failed_at` = the clock at the first failure;
  - invalid → one dead letter with `attempts=1`, no sleeps, handler called once;
  - store returns `False` → `HANDLED`, no exception;
  - store fails twice then succeeds → `HANDLED`, sleeps include `max_delay` twice, and the
    handler was retried from attempt 1 in each round;
  - stop during a retry pause → `STOPPED`, no dead letter;
  - stop during the hold pause → `STOPPED`.
- [X] T032 [US4] Wire it in `src/notifications/main.py`: `RetryPolicy.from_settings(settings)`,
  `MongoDeadLetterStore(db)` and `RetryingMessageProcessor(policy, store, StopAwareSleeper(...),
  SystemClock())`. The sleeper uses the manager's stop event, so expose the event or let the
  manager build the sleeper; keep it a single owner.

  Component tests in `tests/component/test_dead_letters_flow.py`, with
  `CONSUMER_RETRY_BASE_DELAY_SECONDS=0.05` and `CONSUMER_RETRY_MAX_DELAY_SECONDS=0.1` set through
  `Settings` to keep them fast:
  - produce `b"not json"`, then a payload without `customerId`, then a valid event to the same
    partition (same key); `wait_until` two dead letters with `attempts == 1`, the right
    topic/partition/offsets and error types, and one notification for the valid event;
  - the committed offset passes all three messages.

**Checkpoint**: No message can block the partition for longer than the retry budget, and none
can be lost.

---

## Phase 7: User Story 5 — The service keeps running when the event broker is unreachable (Priority: P3)

**Goal**: Consumption survives broker outages: start without Kafka, catch up after it returns, and
reconnect after a mid-run outage. Readiness never fails because of Kafka.

**Independent Test**: Start the app with Kafka paused; health is live and ready, with Kafka not
connected. Unpause, produce an event: its notification is recorded without a restart.

- [X] T033 [US5] In `src/notifications/messaging/consumer_manager.py`, make `_connect` race
  `consumer.start()` against the stop event (research R9), so `stop()` returns promptly while a
  connection attempt hangs.

  Test in `tests/unit/messaging/test_consumer_manager.py`: with an unroutable broker address
  (e.g. `10.255.255.1:9092`) and `request_timeout_seconds=30`, `stop()` returns within 2 s
  (measured with `time.monotonic`, no sleeps).
- [X] T034 [US5] Component tests in `tests/component/test_kafka_outage.py`:
  1. Pause the Kafka container, then start the app with the real lifespan. `/health/live` is 200;
     `/health/ready` is 200 with `kafka` in `{CONNECTING, DISCONNECTED}`. Unpause, produce a
     valid event, then `wait_until` readiness reports `CONNECTED` and the notification exists.
  2. With the app running and connected, pause Kafka. `wait_until` readiness reports
     `DISCONNECTED` and stays 200. Unpause, produce an event, and `wait_until` its notification
     exists.

  Use a short `check_interval_seconds`, injected through a manager parameter exposed for tests,
  to keep the waits short. Always unpause in a `finally`.

**Checkpoint**: All five stories pass independently and together.

---

## Phase 8: Polish & cross-cutting concerns

- [X] T035 [P] Update `README.md`:
  - the configuration table with the four `CONSUMER_RETRY_*` variables (contracts/configuration.md);
  - the "Consumed topics" status (`orders.order-placed` implemented, the others planned);
  - the status note;
  - the dead-letter behaviour (invalid → immediate; failing → retries; store down → hold);
  - the HTTP table unchanged.
- [X] T036 [P] Update `CLAUDE.md`:
  - add a "Where things are (US-01)" section listing handler → service → repositories → processor
    and the index module version 2;
  - move the "Active feature" notes into permanent rules (lossless parsing, `Decimal128`,
    commit-on-`HANDLED`, hold on dead-letter failure, one message at a time).
- [X] T037 [P] Update `../ecommerce-infra/docs/events.md` on a branch
  `docs/notifications-consumes-order-placed` in the ecommerce-infra repository:
  - in the Topics table, `orders.order-placed` → consumed by "Payments, Inventory,
    Notifications", with the Notifications consumer implemented;
  - in the `orders.order-placed` section, a sentence that Notifications uses `customerId` and
    `totalAmount`.

  Open a PR there that links this branch's PR (Principle VIII); do not change the payload.
- [X] T038 Run the full gate and confirm coverage ≥ 80% on domain + application. Run
  quickstart.md steps 2–7 against ecommerce-infra and record any deviation as a fix before the
  PR. Remove the "Active feature" section from `CLAUDE.md` if T036 did not already.
- [X] T039 Build the image (`docker build -t ecommerce-notifications:dev .`) and run it on the
  `ecommerce_default` network (README "Docker"). Check that the image healthcheck is `healthy`
  and that quickstart step 3 works against the container.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)** → **Foundational (Phase 2)** → user stories → **Polish (Phase 8)**.
- **US1 (Phase 3)** depends on Foundational only. It is the MVP.
- **US2 (Phase 4)** depends on US1 (it extends the service, repository and flow test of US1).
- **US3 (Phase 5)** depends on US1 (it extends the service and wiring). It is independent of US2
  and can be done in either order after US1.
- **US4 (Phase 6)** depends on Foundational (processor contract) and on US1 for its component
  test. It is independent of US2 and US3.
- **US5 (Phase 7)** depends on Foundational (manager) and US1 (component flow).
- **Polish** depends on all stories.

### Story completion order

```text
Setup ─► Foundational ─► US1 ─┬─► US2 ─┐
                              ├─► US3 ─┤
                              ├─► US4 ─┼─► Polish
                              └─► US5 ─┘
```

All four stories after US1 touch `src/notifications/main.py` or
`tests/component/test_order_placed_flow.py`. When working on them in parallel, merge those files
carefully; doing them sequentially (US2 → US3 → US4 → US5) is the simple path.

### Within each story

The test is written first and seen to fail, then the implementation follows, then the gate.
Domain → application → infrastructure → messaging → wiring → component test.

## Parallel Opportunities

- **Setup**: T001, T002, T003 (different files).
- **Foundational**: T004, T005, T006, T007 in parallel; then T008 → T009 → T010.
- **US1**: T011, T012 and T015 in parallel; then T013 → T014 → T016 → T017 → T018 → T019.
- **US3**: T023 in parallel with any US2 task.
- **US4**: T027, T028 and T029 in parallel; then T030 → T031 → T032.
- **Polish**: T035, T036 and T037 in parallel.

### Parallel example: User Story 1

```text
Task: "T011 [P] [US1] order_received_message + format_amount in src/notifications/domain/messages.py"
Task: "T012 [P] [US1] Notification aggregate + enums in src/notifications/domain/notification.py"
Task: "T015 [P] [US1] LoggingNotificationSender in src/notifications/infrastructure/sender.py"
```

### Parallel example: User Story 4

```text
Task: "T027 [P] [US4] retry settings in src/notifications/infrastructure/settings.py"
Task: "T028 [P] [US4] RetryPolicy in src/notifications/messaging/retry.py"
Task: "T029 [P] [US4] DeadLetter + DeadLetterStore in src/notifications/messaging/dead_letters.py"
```

## Implementation Strategy

### MVP first

1. Phase 1 + Phase 2. The gate is green, and the manager consumes with the holding processor.
2. Phase 3 (US1). Stop and validate with quickstart step 3: a placed order yields a notification.
3. Phase 4 (US2). Stop and validate with quickstart step 4. US1 + US2 is the shippable MVP:
   correct under redelivery, and nothing is ever skipped (the holding processor).

### Incremental delivery

4. US3 → validate the order context (quickstart step 3, `order_context` query).
5. US4 → validate dead letters (quickstart steps 5–6).
6. US5 → validate outages (quickstart step 7).
7. Polish → README, CLAUDE.md, the ecommerce-infra PR, a full quickstart run, the image check.
   Then open the PR "Implements US-01: Order received notification".

Each step ends with a green gate and a Conventional Commit; the branch is never red.
