# Feature Specification: Order confirmed and order cancelled notifications (US-02)

**Feature Branch**: `feature/US-02-order-confirmed-cancelled-notifications`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "US-02: Record notifications when an order is confirmed or cancelled.
Notifications consumes orders.order-confirmed and orders.order-cancelled and records a
notification for the customer for each event, with the reason of a cancellation when there is
one. Everything from US-01 (idempotency, commit after save, retries, dead letters, startup
without Kafka) applies unchanged, through the same consumer."

## Clarifications

### Session 2026-11-16

- Q: How should messages be worded? → A: Short English templates, deterministic, containing ids
  and amounts only.
- Q: What does a cancellation without a reason say? → A: The generic cancelled text.
- Q: One consumer or one per topic? → A: One consumer subscribed to all topics of the service,
  with a routing table from topic to parser and handler.
- Q: Are the read API or refunds part of this story? → A: No, both are out of scope.
- Q: Does the cancellation reason belong in the message, given "ids and amounts only"? → A: Yes.
  The reason (normalised per FR-007) is included; "ids and amounts only" means no personal data
  beyond `customerId`, as constitution Principle IV states.
- Q: What happens to a cancellation whose `reason` is not text? → A: The message is invalid and
  is dead-lettered at once; no notification is recorded.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Customer is told their order was confirmed (Priority: P1)

When an order is confirmed (its payment was processed and its stock reserved, or an operator
confirmed it), the Orders service announces it with an `orders.order-confirmed` event.
Notifications records one "order confirmed" notification for the customer, addressed to the email
channel, and simulates the send with a log line once the notification is stored.

**Why this priority**: Confirmation is the outcome customers wait for after placing an order.
It is independent of the cancellation story and delivers value on its own.

**Independent Test**: Publish one valid `orders.order-confirmed` event. Exactly one notification
of type `ORDER_CONFIRMED` exists for that customer and order, with the expected message, and one
"sent" log line was written after it was stored.

**Acceptance Scenarios**:

1. **Given** no notification exists for event `E`, **When** an `orders.order-confirmed` event
   with event id `E`, order id `O` and customer `C` is processed, **Then** exactly one
   notification is stored with type `ORDER_CONFIRMED`, channel `email`, status `SENT`, customer
   `C`, order `O`, source event `E`, a creation time in UTC, and the message
   `Your order O was confirmed.`
2. **Given** the notification for `E` has just been stored, **When** processing completes,
   **Then** one log line records the simulated send (the same format as the order received
   notification), and the event is acknowledged only after the notification was stored.
3. **Given** the placement of order `O` was never received by Notifications, **When** the
   confirmation of `O` is processed, **Then** the notification is still recorded, with the
   customer from the confirmation event.

---

### User Story 2 - Customer is told their order was cancelled, and why (Priority: P1)

When a pending order is cancelled, the Orders service announces it with an
`orders.order-cancelled` event. Notifications records one "order cancelled" notification for the
customer. When the event carries a reason (for example `payment failed: amount exceeds the
threshold` or `stock rejected: insufficient stock`), the message includes it; when it does not
(a cancellation requested by the customer through the Orders API), the message is generic.

**Why this priority**: A cancelled order is the case where the customer most needs to be told,
and the reason explains what happened. Same priority as the confirmation.

**Independent Test**: Publish one `orders.order-cancelled` event with a reason and one without.
Two `ORDER_CANCELLED` notifications exist: one whose message ends with the reason, one with the
generic text.

**Acceptance Scenarios**:

1. **Given** an `orders.order-cancelled` event for order `O`, customer `C`, with reason
   `payment failed: amount exceeds the threshold`, **When** it is processed, **Then** exactly one
   notification is stored with type `ORDER_CANCELLED`, channel `email`, status `SENT`, customer
   `C`, order `O`, and the message
   `Your order O was cancelled: payment failed: amount exceeds the threshold.`
2. **Given** an `orders.order-cancelled` event for order `O` without a `reason` field, **When** it
   is processed, **Then** the notification message is `Your order O was cancelled.`
3. **Given** an `orders.order-cancelled` event whose `reason` is empty, only whitespace, or
   `null`, **When** it is processed, **Then** it is treated as having no reason and gets the
   generic message.
4. **Given** the placement of order `O` was never received by Notifications, **When** the
   cancellation of `O` is processed, **Then** the notification is still recorded, with the
   customer from the cancellation event.

---

### User Story 3 - The new topics are as reliable as the first one (Priority: P2)

Confirmations and cancellations get the same guarantees as order placements: a redelivered
event never produces a second notification, a malformed message is set aside without blocking
the others, a failing message is retried and then set aside, and the service keeps running when
the event broker is unreachable. All three order topics are handled by the one consumer the
service already has.

**Why this priority**: Without it, the P1 stories are only correct on the happy path. It reuses
existing behaviour, so it is mainly proven rather than built.

**Independent Test**: Deliver a confirmation twice; publish an invalid cancellation followed by a
valid one; check one notification for the duplicate, one dead letter for the invalid message,
and that the valid one was processed. Check that a single consumer reads all three order topics.

**Acceptance Scenarios**:

1. **Given** event `E` (confirmation or cancellation) was fully processed, **When** `E` is
   delivered again, **Then** no new notification is stored, no second "sent" log line is
   written, no dead letter is created, and the event is acknowledged.
2. **Given** a message on either new topic that is not valid JSON, or misses `eventId`,
   `aggregateId` or `customerId`, or has one of them in the wrong format, **When** it is
   consumed, **Then** it is dead-lettered once (attempts 1) with the reason, nothing is stored,
   and the next message is processed.
3. **Given** storage fails for a valid confirmation or cancellation, **When** it is processed,
   **Then** it is retried with the configured backoff and, if every attempt fails,
   dead-lettered and acknowledged, exactly as for order placements.
4. **Given** the service is running, **When** events arrive on `orders.order-placed`,
   `orders.order-confirmed` and `orders.order-cancelled`, **Then** they are all consumed by the
   same consumer, each routed to the handling of its own topic, and the readiness check reports
   one consumer state for all of them.
5. **Given** the broker is unreachable at startup, **When** it becomes reachable, **Then**
   confirmations and cancellations published meanwhile are processed without a restart.

---

### Edge Cases

- **Confirmation and cancellation for the same order**: the Orders contract allows only one of
  them, but if both arrive (different event ids), each records its own notification; neither is
  rejected.
- **Two confirmations (or two cancellations) for the same order with different event ids**: each
  records its own notification (idempotency is per event, not per order).
- **Reason with surrounding whitespace**: trimmed before use. Line breaks and tabs inside the
  reason are replaced by single spaces so the message stays on one line.
- **Reason of the wrong JSON type** (number, boolean, object, array): the message is invalid and is
  dead-lettered; `null` counts as "no reason" (scenario 2.3).
- **Very long reason**: reasons longer than 500 characters are cut to 500 characters followed by
  `…`, so the stored message stays bounded and deterministic.
- **Reason that already ends with a period** (`payment failed: card expired.`): one trailing
  period is removed before the template adds its own, so the message never ends with `..`.
- **Extra fields** (including `reason` on a confirmation): ignored.
- **Unsupported schema version**: `version` other than `1` is invalid and dead-lettered.
- **Cancellation for an order whose placement arrives later**: both are processed independently;
  the order context recorded by the later placement is unaffected by the cancellation.

## Requirements *(mandatory)*

### Functional Requirements

**Consuming and validating**

- **FR-001**: The service MUST consume `orders.order-confirmed` and `orders.order-cancelled` as
  defined in `ecommerce-infra/docs/events.md`, in addition to `orders.order-placed`, through the
  existing consumer and consumer group. The one consumer subscribes to all topics of the service
  and routes each message through a routing table that maps its topic to that topic's parser and
  handler; no second consuming mechanism may be introduced, and a message on a topic without an
  entry in the table is never silently dropped.
- **FR-002**: A confirmation is valid only if it is a JSON object with `eventId` (UUID),
  `aggregateId` (UUID, the order id), `occurredAt` (ISO 8601 with timezone), `version` equal to
  `1` and `customerId` (UUID). Unknown fields MUST be ignored.
- **FR-003**: A cancellation is valid under the same rules as a confirmation; in addition,
  `reason`, when present and not `null`, MUST be a JSON string. A `reason` of any other JSON type
  (number, boolean, object, array) makes the message invalid: it is dead-lettered at once and no
  notification is recorded.
- **FR-004**: Invalid or unparseable messages MUST NOT be partially processed; they are
  dead-lettered at once (US-01 FR-015).

**Recording**

- **FR-005**: For every valid confirmation the service MUST store exactly one notification with a
  new unique id, customer id (from the event), order id (= `aggregateId`), type
  `ORDER_CONFIRMED`, channel `email`, status `SENT`, the message
  `Your order <orderId> was confirmed.`, creation time (UTC) and source event id (= `eventId`).
- **FR-006**: For every valid cancellation the service MUST store exactly one notification with
  the same fields, type `ORDER_CANCELLED`, and the message:
  - with a reason: `Your order <orderId> was cancelled: <reason>.`
  - without a reason: `Your order <orderId> was cancelled.`
- **FR-007**: The reason MUST be normalised before use: trimmed; line breaks and tabs replaced by
  single spaces; one trailing period removed; cut to 500 characters followed by `…` if longer. An
  empty result means "no reason". The same event MUST always produce the same message.
- **FR-008**: Messages MUST be short, deterministic English templates containing only the order
  id and, for cancellations, the reason supplied by the event (a system text from Orders,
  Payments or Inventory; constitution Principle IV requires it when present). They MUST NOT
  contain personal data beyond the ids.
- **FR-009**: These two topics MUST NOT read or write the order context; the customer is the one
  in the event.
- **FR-010**: After a notification is stored, the service MUST write the simulated send log line,
  in the same format as US-01, once; never before the notification is stored and never for a
  duplicate.
- **FR-011**: Stored notifications MUST NOT be changed or deleted.

**Reliability (unchanged from US-01, now applying to the new topics)**

- **FR-012**: At most one notification MUST exist per source event id across all notification
  types; a redelivered event stores nothing new and is not an error.
- **FR-013**: Offsets MUST be committed manually and only after the notification is stored (or
  recognised as already stored) or the message is dead-lettered.
- **FR-014**: Retry with backoff, dead-lettering after the last attempt, holding the message while
  the dead-letter store is unavailable, and one-message-at-a-time processing MUST apply to the new
  topics exactly as configured for `orders.order-placed`.
- **FR-015**: The service MUST start and stay ready while the broker is unreachable; the readiness
  check keeps reporting one consumer state.

**Configuration and documentation**

- **FR-016**: The topic names MUST be settings with the contract names as defaults
  (`orders.order-confirmed`, `orders.order-cancelled`, and `orders.order-placed` for consistency),
  and each consumed topic MUST be added to the consumer as one routing-table registration. The new
  settings are documented in the README.
- **FR-017**: The README ("Consumed topics" and processing guarantees), CLAUDE.md and
  `ecommerce-infra/docs/events.md` (Notifications consumer marked implemented for both topics)
  MUST be updated in the same change set.

### Key Entities

- **Notification** (existing): gains the types `ORDER_CONFIRMED` and `ORDER_CANCELLED`. Same
  attributes and rules as in US-01; immutable; unique per source event.
- **Cancellation reason** (value, not stored on its own): the optional free text explaining a
  cancellation, normalised as in FR-007 and embedded in the notification message.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every valid confirmation and cancellation, exactly one notification of the
  matching type exists for the customer: 100% recorded, 0 duplicates after repeated delivery.
- **SC-002**: Under normal conditions a confirmation or cancellation is recorded within 5 seconds
  of being published.
- **SC-003**: 100% of cancellation notifications whose event carries a reason show that reason;
  0% of those without a reason show a reason.
- **SC-004**: No message on any of the three order topics is lost: each ends as a notification
  or as a dead letter with its reason.
- **SC-005**: A confirmation or cancellation is recorded whether or not the placement of the same
  order was ever received (0 dependencies between the three order topics).

## Assumptions

- **Contract**: `ecommerce-infra/docs/events.md` is the source of truth. Only `version` 1 exists
  for both events. A confirmation carries no reason; a `reason` field on it is ignored.
- **Templates**: `Your order <orderId> was confirmed.`; `Your order <orderId> was cancelled:
  <reason>.`; `Your order <orderId> was cancelled.` The cancellation templates follow the example
  in the request; the confirmation template mirrors the US-01 wording.
- **Reason length cap**: 500 characters. Orders builds reasons from short outcome texts
  (`payment failed: …`, `stock rejected: …`), so the cap only guards against abnormal input.
- **"Configuration"** for the new topics: the topic names become settings whose defaults are the
  contract names (decided at planning, replacing US-01 research R8's constant), and the consumer
  gains one routing-table entry per topic.
- **Same notification channel and status** as US-01: `email`, `SENT`.
- **Index schema** is unchanged: the unique source event index already covers every notification
  type.
- **Out of scope**: the refund notification (US-03), any HTTP endpoint beyond health (US-04),
  real email sending, replay of dead-letter messages.
