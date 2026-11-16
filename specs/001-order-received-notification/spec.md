# Feature Specification: Order received notification (US-01)

**Feature Branch**: `feature/US-01-order-received-notification`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "US-01: Record an 'order received' notification when an order is placed.
Notifications consumes orders.order-placed and records a notification for the customer, also
remembers the order's customer and total for later stories, is idempotent by event, retries
failures and sets aside messages it cannot process, and keeps running when the event broker is
unreachable."

## Clarifications

### Session 2026-11-16

- Q: What happens when the dead-letter record cannot be stored? → A: Hold the partition: go back
  to the message and process it again from the start (retries included) with the capped backoff,
  until it is stored or dead-lettered. Never move past it and never acknowledge it otherwise.
- Q: When one message is being retried, what else waits? → A: Messages are processed one at a
  time per service instance, so the whole instance (all partitions it reads) waits during the
  retries, for at most the retry budget.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Customer is told their order was received (Priority: P1)

When a customer places an order, the Orders service announces it with an `orders.order-placed`
event. Notifications records one "order received" notification for that customer, addressed to
the email channel, and simulates the send by writing a log line once the notification is stored.

**Why this priority**: This is the reason the story exists: the first customer-facing
notification of the order lifecycle. Everything else in this story protects its correctness.

**Independent Test**: Publish one valid `orders.order-placed` event and check that exactly one
notification of type `ORDER_RECEIVED` exists for that customer and order, with the expected
message, and that one "sent" log line was written after it was stored.

**Acceptance Scenarios**:

1. **Given** no notification exists for order `O` and the event is valid, **When** an
   `orders.order-placed` event with event id `E`, order id `O`, customer `C` and total `129.90`
   is processed, **Then** exactly one notification is stored with type `ORDER_RECEIVED`, channel
   `email`, status `SENT`, customer `C`, order `O`, source event `E`, a creation time in UTC, and
   the message `Your order O for 129.90 was received.`
2. **Given** the notification for event `E` has just been stored, **When** processing completes,
   **Then** one log line records the simulated send with the notification id, type, channel,
   customer id and order id, and the event's position in the stream is acknowledged only after
   the notification was stored.
3. **Given** two different `orders.order-placed` events for two different orders, **When** both
   are processed, **Then** two notifications exist, one per order, each with its own source
   event.

---

### User Story 2 - The same event never produces a second notification (Priority: P1)

Events can be delivered more than once (for example after a crash between storing the result and
acknowledging the event). The customer must still see exactly one "order received" notification
per order placement, and a repeated delivery must not be treated as an error.

**Why this priority**: A duplicate notification is a visible defect for the customer, and
redelivery is guaranteed to happen in an at-least-once system. It has the same priority as the
happy path.

**Independent Test**: Process the same event twice (same event id) and check that one
notification and one order context exist, that only one "sent" log line was written, and that the
second delivery is acknowledged without error and without a dead-letter record.

**Acceptance Scenarios**:

1. **Given** event `E` was fully processed, **When** `E` is delivered again, **Then** no new
   notification is stored, no second "sent" log line is written, the order context is unchanged,
   no dead-letter record is created, and the event is acknowledged.
2. **Given** the order context for `E` was stored but the service stopped before the notification
   was stored, **When** `E` is delivered again, **Then** the order context is unchanged and the
   notification is stored once.
3. **Given** the notification for `E` was stored but the service stopped before acknowledging the
   event, **When** `E` is delivered again, **Then** nothing new is stored and the event is
   acknowledged.

---

### User Story 3 - The order's customer is remembered for later notifications (Priority: P2)

Some later events (refunds, US-03) identify only the order, not the customer. When an order is
placed, Notifications remembers the order's customer and total so that those later notifications
can be addressed to the right customer.

**Why this priority**: Not visible to the customer in this story, but a refund notification
cannot be built without it, and the data is only available from this event.

**Independent Test**: Process a valid `orders.order-placed` event and check that an order context
exists for the order id with the event's customer and total and a recording time in UTC.

**Acceptance Scenarios**:

1. **Given** no order context exists for order `O`, **When** an `orders.order-placed` event for
   `O`, customer `C`, total `129.90` is processed, **Then** an order context `O → C, 129.90` exists
   with a recording time in UTC.
2. **Given** an order context for `O` already exists, **When** the same event is processed again,
   **Then** the order context keeps its values, including its original recording time.

---

### User Story 4 - Messages that cannot be processed are set aside without blocking others (Priority: P2)

A message that is malformed, or that keeps failing because a dependency is down, must not stop
the processing of the messages behind it, and must not disappear silently. Malformed messages are
set aside immediately; messages that fail for a transient reason are retried with growing pauses
and set aside only when the retries are used up. Operators can find every set-aside message,
with the reason, in the dead-letter store.

**Why this priority**: Without it, one bad message blocks every later order of its partition, or
is lost. It protects the P1 stories under failure.

**Independent Test**: Publish a message with invalid content followed by a valid event and check
that the invalid one is in the dead-letter store with its reason (after a single attempt) and that
the valid one produced its notification. Separately, make storage fail for one event and check
that it is attempted the configured number of times, then dead-lettered, and that processing
continues with the next message.

**Acceptance Scenarios**:

1. **Given** a message that is not valid JSON, **When** it is consumed, **Then** it is stored in
   the dead-letter store once with the original payload, topic, partition, offset, the parse
   error, attempt count 1 and timestamps; it is not retried; an error log line with the same
   context is written; and the message is acknowledged.
2. **Given** a message that is valid JSON but misses `eventId`, `aggregateId`, `customerId` or
   `totalAmount`, or has one of them in the wrong format, **When** it is consumed, **Then** it is
   dead-lettered as in scenario 1 with a reason that names the invalid fields, and nothing is
   stored in the notifications or the order context.
3. **Given** storage fails for a valid event, **When** it is processed, **Then** it is retried with
   exponentially growing pauses (bounded by the configured maximum) up to the configured number of
   attempts; if an attempt succeeds, the result is the same as in User Story 1 and no dead-letter
   record exists.
4. **Given** storage keeps failing for a valid event, **When** the last attempt fails, **Then** the
   message is dead-lettered with the last error and the number of attempts made, logged with full
   context, and acknowledged; the next message of the partition is then processed.
5. **Given** a message was dead-lettered, **When** the same message (same topic, partition and
   offset) is delivered again after a crash, **Then** no second dead-letter record is created.

---

### User Story 5 - The service keeps running when the event broker is unreachable (Priority: P3)

Operators can start the service, and it stays healthy, while the event broker is down. The
consumer connects in the background, reconnects by itself, and catches up on the waiting events
once the broker is back. The readiness check shows the consumer state without failing.

**Why this priority**: Already in place from the project bootstrap for the connection itself;
this story extends it to actual consumption, so it mainly needs to be preserved and proven.

**Independent Test**: Start the service with the broker unreachable, check that it answers health
checks and reports the consumer as disconnected, then make the broker available, publish an
event and check that its notification is recorded without restarting the service.

**Acceptance Scenarios**:

1. **Given** the broker is unreachable, **When** the service starts, **Then** startup completes,
   the liveness check answers `UP`, and the readiness check answers ready with the consumer state
   `CONNECTING` or `DISCONNECTED`.
2. **Given** the service started while the broker was unreachable, **When** the broker becomes
   reachable, **Then** the consumer connects without a restart, the readiness check reports
   `CONNECTED`, and events published meanwhile are processed.
3. **Given** the consumer is connected, **When** the broker becomes unreachable, **Then** the
   service keeps answering health checks, readiness stays ready and reports the consumer as not
   connected, and the consumer reconnects by itself when the broker returns.

---

### Edge Cases

- **Extra or unknown fields** in the event (including `items`, which this story does not use) are
  ignored; they never make a message invalid.
- **Unsupported schema version**: an event whose `version` is not `1` is invalid and is
  dead-lettered without retries.
- **Negative total**: an event with a `totalAmount` below zero is invalid. A total of zero is
  valid.
- **Total with more than two decimals**: the message shows the total rounded to two decimals
  (half up); the order context keeps the exact value from the event.
- **Same order id, different event id** (a contract violation by the producer): the second event
  records its own notification (different source event); the order context keeps the values of
  the first event, and a warning is logged.
- **Message without key or with a key that differs from `aggregateId`**: the key is ignored; the
  order is identified by `aggregateId` only.
- **Payload that is not valid text** (cannot be decoded): dead-lettered like bad JSON, with the
  payload kept in an encoded form so it is not lost.
- **The dead-letter store itself is unavailable**: the message is not acknowledged and the
  consumer does not move past it. It goes back to that message and processes it again from the
  start (retries included), pausing with the capped backoff between rounds, until it is stored or
  dead-lettered. Later messages of the partition wait; none of them is acknowledged meanwhile,
  because acknowledging a later message would implicitly acknowledge this one. Readiness reports
  the database as down during the outage.
- **Shutdown while a message is being processed**: the message is either completed and
  acknowledged, or left unacknowledged and delivered again after restart; it is never
  acknowledged half-done.
- **Database unreachable at startup**: startup fails (indexes cannot be applied), as today.

## Requirements *(mandatory)*

### Functional Requirements

**Consuming and validating**

- **FR-001**: The service MUST consume the `orders.order-placed` topic as defined in
  `ecommerce-infra/docs/events.md`, using its configured consumer group.
- **FR-002**: The service MUST treat a message as valid only if it is a JSON object with
  `eventId` (UUID), `aggregateId` (UUID, the order id), `occurredAt` (ISO 8601 timestamp with
  timezone), `version` equal to `1`, `customerId` (UUID) and `totalAmount` (number, zero or
  greater). Unknown fields MUST be ignored.
- **FR-003**: An invalid or unparseable message MUST NOT be partially processed: nothing is stored
  in the notifications or the order context for it.

**Recording**

- **FR-004**: For every valid event, the service MUST store an order context for the order: order
  id, customer id, total amount and recording time (UTC). If one already exists for the order id,
  it MUST be left unchanged (first write wins); if its customer or total differs from the event, a
  warning MUST be logged.
- **FR-005**: For every valid event, the service MUST store exactly one notification with: a new
  unique id, customer id, order id (= `aggregateId`), type `ORDER_RECEIVED`, channel `email`,
  status `SENT`, message, creation time (UTC) and source event id (= `eventId`).
- **FR-006**: The message MUST be produced by a fixed template:
  `Your order <orderId> for <totalAmount> was received.`, with `<totalAmount>` written with two
  decimals (rounded half up) and no currency. The same event MUST always produce the same message.
  The message MUST NOT contain personal data other than ids and amounts.
- **FR-007**: The order context MUST be stored before the notification, so that an interrupted
  processing is completed by the redelivery.
- **FR-008**: Stored notifications MUST NOT be changed or deleted by this feature.
- **FR-009**: After a notification is stored, the service MUST write one log line that simulates
  the email send (notification id, type, channel, customer id, order id, source event id). No log
  line is written before the notification is stored, nor for a duplicate.

**Idempotency and acknowledgement**

- **FR-010**: At most one notification MUST exist per source event id, enforced by the data store
  (unique constraint), not only by application checks.
- **FR-011**: Processing an event whose notification already exists MUST store nothing new, MUST
  NOT be reported or handled as a failure, and MUST be acknowledged.
- **FR-012**: The event's position in the stream MUST be acknowledged (committed) manually, and
  only after the order context and the notification are stored (or recognised as already stored),
  or after the message is dead-lettered. Automatic acknowledgement MUST be disabled.

**Failures and dead letters**

- **FR-013**: A valid event whose processing fails MUST be retried with exponential backoff:
  the pause before retry `n` is `base delay × multiplier^(n-1)`, capped at the maximum delay, up to
  the configured maximum number of attempts (the first attempt included). Messages are processed
  one at a time per service instance: while a message is being retried, the instance processes no
  other message, from any partition.
- **FR-014**: When the last attempt fails, the service MUST store a dead-letter record, write an
  error log line with full context, and acknowledge the message so the partition is not blocked.
- **FR-015**: Invalid or unparseable messages (FR-002) MUST be dead-lettered on the first attempt,
  without retries.
- **FR-016**: A dead-letter record MUST contain: the original payload (as text, or encoded when it
  is not valid text), the original key if any, topic, partition, offset, error type and message,
  number of attempts, the time of the first failure and the time the record was stored.
- **FR-017**: At most one dead-letter record MUST exist per topic, partition and offset; a
  redelivered message that was already dead-lettered MUST NOT create a second one.
- **FR-018**: If the dead-letter record cannot be stored, the message MUST NOT be acknowledged
  and the consumer MUST NOT move past it: it MUST return to that message and process it again
  from the start (retries included), pausing with the capped backoff between rounds, until the
  message is stored or dead-lettered. No later message of the same partition may be acknowledged
  before then.

**Startup, health and configuration**

- **FR-019**: The service MUST start and answer health checks when the broker is unreachable; the
  consumer MUST connect and reconnect in the background, and the readiness check MUST report the
  consumer state without becoming unready because of it.
- **FR-020**: The following indexes MUST be declared in the versioned index module and created at
  startup: unique on the notification's source event id; notifications by customer id and creation
  time (newest first); unique order context by order id; unique dead letter by topic, partition
  and offset. The index schema version MUST be increased.
- **FR-021**: The retry settings (maximum attempts, base delay, multiplier, maximum delay) MUST be
  configurable through environment variables with defaults, and every new setting MUST be
  documented in the README.
- **FR-022**: The README, CLAUDE.md and `ecommerce-infra/docs/events.md` MUST be updated in the same
  change set: Notifications listed as a consumer of `orders.order-placed`, marked implemented.

### Key Entities

- **Notification**: A record that a customer was told about an order event. Attributes: id,
  customer id, order id, type (`ORDER_RECEIVED` in this story), channel (`email`), status (`SENT`),
  message, creation time (UTC), source event id (unique). Immutable once stored.
- **Order context**: What Notifications knows about an order from its placement. Attributes: order
  id (unique), customer id, total amount, recording time (UTC). Used by later stories to find the
  customer of an order.
- **Inbound dead letter**: A consumed message that could not be processed. Attributes: original
  payload and key, topic, partition, offset (unique together), error type and message, attempts,
  first failure time, stored time.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every valid order placement, exactly one "order received" notification exists
  for the customer: 100% of valid events produce one, and 0 duplicates exist after the same events
  are delivered two or more times.
- **SC-002**: Under normal conditions, the notification for an order is recorded within 5 seconds
  of the order being placed.
- **SC-003**: No message is lost: every consumed message ends either as a recorded notification
  or as a dead-letter record with its reason (0 messages acknowledged without one or the other).
- **SC-004**: A malformed message delays the messages behind it by less than 1 second; a
  persistently failing message delays them by no more than the configured retry budget (about 15
  seconds with the defaults).
- **SC-005**: With the broker unreachable, the service is up and reports ready within 10 seconds
  of start, and it processes waiting events within 30 seconds of the broker becoming reachable
  again, without a restart.
- **SC-006**: 100% of set-aside messages can be found by an operator with the reason they failed
  and enough context (topic, partition, offset) to locate the original.

## Assumptions

- **Event contract**: `ecommerce-infra/docs/events.md` is the source of truth. Only `version` 1 of
  `orders.order-placed` exists; a different version is treated as invalid rather than guessed at.
  `items` is not used by this story and is not validated.
- **Total amount**: `orders.order-placed` carries no currency, so the message shows the amount
  without a currency. The exact amount is kept in the order context for later stories.
- **Creation time** is the time the notification is first stored, not the event's `occurredAt`;
  a redelivery does not change it because nothing new is stored.
- **First write wins** for the order context: a placement is announced once per order, so a
  conflicting second event is a producer error that is logged, not applied.
- **Retry defaults**: 5 attempts in total, 1 second base delay, multiplier 2, maximum delay
  10 seconds (pauses of 1, 2, 4 and 8 seconds, about 15 seconds in total). Retries happen in the
  consumer, and messages are processed one at a time per instance, so a failing message holds
  back every partition that instance reads for that time. Retryable failures are almost always
  the database being unavailable, which would block every partition anyway; running more
  instances spreads the partitions across them.
- **Dead-letter replay** is out of scope; dead-letter records are only stored and logged.
- **Out of scope**: the other topics (`orders.order-confirmed`, `orders.order-cancelled`,
  `payments.payment-refunded`: US-02, US-03), any HTTP endpoint beyond health (US-04), real email
  sending.
- **Infrastructure**: the shared MongoDB is standalone (no multi-document transactions), as in
  ADR 0001 and ADR 0002; each write is idempotent by itself. The consumer reconnection behaviour
  from the bootstrap is reused and extended to consumption.
- **Contract names** (topic names, field names such as `eventId` and `aggregateId`) appear in this
  spec because they are the agreed business contract between services, not implementation
  choices.
