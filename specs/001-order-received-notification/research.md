# Research: Order received notification (US-01)

Each entry: **Decision**, **Rationale**, **Alternatives considered**. Findings marked *verified*
were checked against the installed library versions (pydantic 2.14, pymongo 4.20, aiokafka 0.15,
testcontainers 4.15) on 2026-11-16.

## R1. Parsing the payload without losing amount precision

**Decision**: Decode the message value as strict UTF-8, parse it with the standard library
`json.loads(text, parse_float=Decimal)`, check that the result is a JSON object, then validate it
with the Pydantic model in Python mode (`OrderPlacedEvent.model_validate(obj)`). Fields that the
contract types as JSON numbers, integers or strings get a small `BeforeValidator` that rejects
values of the wrong JSON type before Pydantic's lax coercion runs:

| Field | Accepts | Rejects |
|---|---|---|
| `totalAmount` | JSON number (`Decimal` from a float literal, or `int`, converted to `Decimal`); finite; `>= 0` | strings, booleans, `NaN`/`Infinity`, negatives |
| `version` | JSON integer equal to `1` | `true`, `1.0`, `"1"`, other integers |
| `occurredAt` | ISO 8601 string with a timezone | numbers, naive timestamps |
| `eventId`, `aggregateId`, `customerId` | UUID strings | anything else |

**Rationale** (*verified*):
- `model_validate_json` turns `123456789012345678.123456789` into
  `Decimal('123456789012345680')`: large amounts lose digits. `json.loads(..., parse_float=Decimal)`
  keeps `Decimal('123456789012345678.123456789')`.
- In JSON mode with `strict=True`, a `Decimal` field still accepts the string `"129.90"`, which
  the contract does not allow.
- In Python mode, `Literal[1]` accepts `True` and `Decimal('1.0')` because they compare equal to
  `1`. An explicit JSON-type check is therefore needed for `version`.
- `json.loads` maps `NaN` to a float, which the amount validator rejects.

**Alternatives considered**:
- `model_validate_json` with `strict=True` on the model: lossy for large amounts, and strict mode
  in Python would reject UUID strings.
- Treating amounts as `float`: not acceptable for money (the order context keeps the exact
  amount for later stories).

## R2. Storing amounts and timestamps in MongoDB

**Decision**: Store `totalAmount` as BSON `Decimal128` (`Decimal128(str(amount))`), converted
explicitly in the persistence mapping, and read it back with `.to_decimal()`. Store timestamps as
BSON dates from timezone-aware UTC `datetime`s. The client already uses `tz_aware=True`, so values
come back as aware UTC datetimes.

**Rationale** (*verified*): `bson.encode` raises `InvalidDocument` for a plain `Decimal`.
`Decimal128` round-trips `129.90` exactly. BSON dates have millisecond precision, so the domain
truncates `createdAt` and `recordedAt` to milliseconds when it creates them. Then the value held
in memory and the stored value are equal.

**Alternatives considered**: a PyMongo `TypeRegistry` codec for `Decimal` (implicit, and it hides
the mapping that Principle I wants explicit); storing amounts as strings (no numeric queries
later).

## R3. Idempotent writes without transactions

**Decision** (per ADR 0002):
- **Order context**: `find_one_and_update({"_id": orderId}, {"$setOnInsert": {...}},
  upsert=True, return_document=BEFORE)`. `None` means it was inserted. A document means it
  already existed; it is returned to the application, which asks the domain whether it conflicts
  and logs a warning if so. The order id is the document `_id`, which is unique by definition.
- **Notification**: `insert_one`. A `DuplicateKeyError` whose `details["keyPattern"]` is
  exactly `{"sourceEventId": 1}` is mapped to "already recorded" (`add` returns `False`). Any
  other `DuplicateKeyError`, and any other `PyMongoError`, propagates as a real failure.
- **Dead letter**: `insert_one` under a unique index on `(topic, partition, offset)`. A duplicate
  on that index means "already dead-lettered", which counts as success.
- **Write order** in the application service: order context first, then the notification (FR-007).
- Concurrent upserts of the same `_id` can make one of them fail with a duplicate key on `_id`.
  The upsert is retried once inside the repository; this is the documented MongoDB pattern for
  upsert races.

**Rationale**: Each write is idempotent on its own, so any crash point is completed by
redelivery. Using `_id` for the order id saves an index.

**Alternatives considered**: a processed-events collection (needs a transaction to be atomic with
the writes; rejected in ADR 0002); `update_one` with `upsert=True` (doesn't return the existing
document needed for the conflict warning).

## R4. Consumer loop, manual commit and "hold the partition"

**Decision**:
- `AIOKafkaConsumer` subscribed to `orders.order-placed`, with `enable_auto_commit=False` and
  `auto_offset_reset="earliest"`, reading with `getmany(timeout_ms=500, max_records=1)`. Messages
  are processed **one at a time per instance** (clarification Q2).
- Each message goes to a `MessageProcessor`, which owns retry and dead-lettering:
  1. Parse. On `InvalidMessageError`, dead-letter at once (attempts = 1).
  2. Otherwise call the handler. On failure, retry with backoff up to `max_attempts`, then
     dead-letter with the last error.
  3. If storing the dead letter fails, wait the capped backoff and restart from step 1 for the
     same message (clarification Q1). This repeats until the message is handled or a stop is
     requested.
  4. Return `HANDLED` or `STOPPED`.
- The manager commits `{TopicPartition(topic, partition): offset + 1}` only for `HANDLED`. On
  `STOPPED` it commits nothing, and the message is redelivered after restart.
- A failed commit (for example `CommitFailedError` after a rebalance) is logged as a warning and
  not retried. The message is redelivered to whoever owns the partition, and processing is
  idempotent.

**Rationale**:
- Kafka commits are cumulative per partition, so the processor must never return until the
  message is either stored or dead-lettered.
- Holding the partition by looping in place is equivalent to seeking back to the offset, and it
  is simpler: no `seek` is needed, and getmany never returns a later message meanwhile.
- One message at a time per instance follows from Q2 and keeps ordering trivially correct.

**Alternatives considered**:
- `consumer.seek(tp, offset)` and re-fetching: same effect, more moving parts.
- Per-partition worker tasks: rejected in Q2.
- Committing every N messages: fewer commits, but more redelivery after a crash. The volume is
  low, so commit per message.

**Known limit**: if the hold loop lasts longer than `max_poll_interval_ms` (300 s default), the
group may rebalance. The commit then fails and the message is redelivered; this is safe because
processing is idempotent.

## R5. Which failures are retried

**Decision**:
- `InvalidMessageError` covers bad UTF-8, bad JSON, a non-object payload and validation errors.
  It is never retried.
- Every other exception raised by the handler is retryable: `PyMongoError`, domain errors raised
  by unexpected data, and unexpected bugs. The processor catches `Exception` at this single
  boundary and handles it explicitly (retry, then dead-letter, log with context), which
  Principle V allows ("`except Exception` without re-raise or handling is prohibited").
- `asyncio.CancelledError` is not an `Exception` subclass. It is never caught, so shutdown still
  works.

**Rationale**: Validation failures are deterministic, and retrying them only delays the
partition. A bug may be data-dependent, and a few retries cost seconds.

**Alternatives considered**: retrying only an allow-list of `PyMongoError` types (an unknown bug
would then skip the retries but still be dead-lettered; the difference is small, and the uniform
rule is simpler).

## R6. Backoff policy

**Decision**: `RetryPolicy(max_attempts, base_delay, multiplier, max_delay)` is a frozen
dataclass in `messaging`. `delay_before_attempt(n) = min(base × multiplier^(n-2), max_delay)`
for attempts `n ≥ 2`. Defaults: 5, 1.0 s, 2.0, 10.0 s, giving pauses of 1, 2, 4 and 8 s. The
same capped delay (`max_delay`) is used between hold-loop rounds. Waits go through an injected
`Sleeper` protocol that wakes up early on stop; tests inject a recording fake, so unit tests
never wait.

**Rationale**: Matches FR-013 and the spec defaults. No jitter, because there is one consumer per
partition and nothing to de-synchronise.

**Alternatives considered**: a retry library (tenacity) would duplicate a few lines, and the
Technology Stack requires an ADR for new dependencies that duplicate capabilities.

## R7. Simulated send

**Decision**: An application port `NotificationSender.send(notification)` with an infrastructure
adapter `LoggingNotificationSender`. It writes one INFO line on the `notifications.send` logger:
`Simulated email sent: notification=<id> type=ORDER_RECEIVED channel=email customer=<customerId>
order=<orderId> sourceEvent=<eventId>`. The application service calls it only when the insert
created the notification.

**Rationale**: Keeps "send only after store, never for a duplicate" in tested application code,
and leaves room for a real provider later without touching the service.

## R8. Settings

**Decision**: Four new settings with validation:
- `CONSUMER_RETRY_MAX_ATTEMPTS`: int, ≥ 1, default 5
- `CONSUMER_RETRY_BASE_DELAY_SECONDS`: float, > 0, default 1.0
- `CONSUMER_RETRY_MULTIPLIER`: float, ≥ 1, default 2.0
- `CONSUMER_RETRY_MAX_DELAY_SECONDS`: float, ≥ base delay, default 10.0

The topic name stays a constant: it is part of the contract, not a deployment choice.

## R9. Startup and shutdown of the manager

**Decision**:
- The bootstrap behaviour is kept: start in the background, back off on connection failure,
  watch metadata every 10 s, report the state.
- The manager now subscribes to the topics of its registered handlers and runs the consume loop
  while connected.
- Fixed in passing: `start()` of the consumer now races the stop event, so a shutdown while
  connecting to an unanswering broker no longer waits up to the request timeout.
- Shutdown sets the stop event. The processor's sleeper wakes up, the in-flight message returns
  `STOPPED` uncommitted, and the consumer is closed.

## R10. Component tests with Kafka

**Decision**:
- Use `testcontainers.kafka.KafkaContainer("confluentinc/cp-kafka:7.6.1")`, the same image as
  ecommerce-infra.
- Topics are created explicitly with `AIOKafkaAdminClient` (3 partitions), as `kafka-init` does
  in the infra; tests don't rely on auto-creation.
- Events are produced with `AIOKafkaProducer`.
- Outage scenarios pause and unpause the container (`docker pause`). This keeps the mapped port,
  so the app's bootstrap address stays valid.
- Assertions use the `wait_until` polling helper and query MongoDB directly. The read API comes
  in US-04.

**Rationale**: Real broker, real offsets and real redelivery (Principle VII). A pause simulates
an unreachable broker without changing addresses.

**Alternatives considered**: stopping and restarting the container (a new mapped port breaks the
bootstrap address); Redpanda (a different broker than production).

## R11. Contract file in ecommerce-infra

**Decision**:
- Update `ecommerce-infra/docs/events.md` in a linked PR on that repository, as part of this
  change set.
- Topics table: add Notifications as a consumer of `orders.order-placed` (implemented).
- `orders.order-placed` section: note that Notifications uses `customerId` and `totalAmount`.
- The payload does not change.
