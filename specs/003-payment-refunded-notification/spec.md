# Feature Specification: Payment refunded notification (US-03)

**Feature Branch**: `feature/US-03-payment-refunded-notification`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "US-03: Record a notification when a payment is refunded.
Notifications consumes payments.payment-refunded. The order is identified by the orderId field
(never aggregateId, which is the payment id). The event has no customerId, so the customer is found
through the order context stored by US-01. A missing order context is a retryable failure that
ends in the dead-letter collection; a notification is never stored without a customer."

## Clarifications

### Session 2026-11-16

- Q: What happens when no order context exists for the refund's order? → A: A retryable failure,
  then the dead-letter collection once the retries are exhausted (as in the spec).
- Q: How is the amount shown? → A: Two decimals, followed by the currency code
  (`129.90 EUR`).
- Q: What about an order context that arrives after the refund was dead-lettered? → A: Accepted
  limitation; it MUST be documented in the README. Replaying dead letters stays out of scope.
- Q: Is any HTTP API part of this story? → A: No, out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Customer is told their payment was refunded (Priority: P1)

When a cancelled order had a processed payment, Payments refunds it and announces it with a
`payments.payment-refunded` event. Notifications looks up the customer of the order from the order
context remembered when the order was placed (US-01), records one "payment refunded" notification
for that customer with the refunded amount and currency, and simulates the send with a log line
once the notification is stored.

**Why this priority**: The refund is the last customer-facing step of a cancelled, paid order; it
tells the customer their money is coming back. It is the whole value of this story.

**Independent Test**: With an order context for order `O` (customer `C`), publish one
`payments.payment-refunded` event for `O`. Exactly one `PAYMENT_REFUNDED` notification exists for
`C` and `O`, with the amount and currency in the message, and one "sent" log line was written after
it was stored.

**Acceptance Scenarios**:

1. **Given** an order context for order `O` with customer `C`, **When** a
   `payments.payment-refunded` event with event id `E`, payment id `P` (as `aggregateId`), order id
   `O`, amount `129.90` and currency `EUR` is processed, **Then** exactly one notification is stored
   with type `PAYMENT_REFUNDED`, channel `email`, status `SENT`, customer `C`, order `O` (not `P`),
   source event `E`, a creation time in UTC, and the message
   `Your payment of 129.90 EUR for order O was refunded.`
2. **Given** the notification for `E` has just been stored, **When** processing completes, **Then**
   one log line records the simulated send (same format as the other notifications), and the event
   is acknowledged only after the notification was stored.
3. **Given** the event also carries a `customerId` field (not part of the contract), **When** it is
   processed, **Then** the field is ignored and the customer still comes from the order context.

---

### User Story 2 - A refund for an unknown order is never lost or misaddressed (Priority: P1)

The customer of a refund can only come from the order context. If it is not there yet (for example
the order placement has not been processed), the refund is retried with growing pauses, and if the
context still does not exist when the retries are used up, the refund is set aside in the
dead-letter store with a clear reason. It is never skipped silently, and a notification is never
stored without a customer.

**Why this priority**: Sending a refund notice to nobody, or to the wrong person, is worse than not
sending it. This story is what makes US-1 safe.

**Independent Test**: Publish a refund for an order without an order context. Check that it is
attempted the configured number of times, then stored in the dead-letter store with an error that
names the missing order context, that no notification exists, and that the next message is
processed.

**Acceptance Scenarios**:

1. **Given** no order context for order `O`, **When** a refund for `O` is processed, **Then** it is
   retried with the configured exponential backoff, and no notification is stored during the
   retries.
2. **Given** the order context for `O` is recorded while the refund is being retried (by another
   service instance, since the instance retrying the refund cannot process the placement meanwhile;
   see Edge Cases), **When** the next attempt runs, **Then** the notification is recorded for the
   context's customer and no dead letter is created.
3. **Given** no order context for `O` appears before the last attempt, **When** the last attempt
   fails, **Then** the message is dead-lettered with the number of attempts, an error type that
   identifies a missing order context, and a message naming order `O`; the failure is logged with
   full context; the offset is committed; no notification exists for the event.

---

### User Story 3 - The refund topic is as reliable as the order topics (Priority: P2)

Refunds get the same guarantees as the other events: a redelivered refund never produces a second
notification, a malformed message is set aside at once without blocking others, and the service
keeps running when the broker is unreachable. The refund topic is one more entry of the consumer's
routing table; no second consuming mechanism exists.

**Why this priority**: Reuses existing behaviour; it mainly needs to be proven for the new topic.

**Independent Test**: Deliver the same refund twice; publish an invalid refund followed by a valid
one; check one notification for the duplicate, one dead letter for the invalid message (one
attempt), and that the valid one was processed.

**Acceptance Scenarios**:

1. **Given** refund `E` was fully processed, **When** `E` is delivered again, **Then** no new
   notification is stored, no second "sent" log line is written, no dead letter is created, and
   the event is acknowledged.
2. **Given** a refund message that is not valid JSON, or misses or has an invalid `eventId`,
   `orderId`, `amount` or `currency`, **When** it is consumed, **Then** it is dead-lettered once
   (attempts 1) with the reason, without retries, and nothing is stored.
3. **Given** the service is running, **When** refunds and order events arrive, **Then** one consumer
   reads all four topics and routes each message to the handling of its topic.

---

### Edge Cases

- **`aggregateId` is the payment id**: it is validated as a UUID but never used as the order id;
  the order is always `orderId`.
- **Refund arrives before the placement is processed**: retried, then dead-lettered (US-2). This is
  expected only when Notifications is catching up on a backlog: a refund follows a cancellation,
  which follows the placement. Because a retrying message holds back the whole instance (US-01
  clarification Q2), the placement cannot be processed by the same instance during those retries;
  the refund then ends in the dead-letter store. Replaying dead letters is out of scope.
- **Two refunds for the same order with different event ids**: each records its own notification.
- **Amount with more or fewer than two decimals**: shown with two decimals, rounded half up
  (`129.9` → `129.90`, `10.005` → `10.01`), as in US-01. A negative or non-finite amount, or an
  amount that is not a JSON number, makes the message invalid. An amount of zero is valid.
- **Currency not a 3-letter uppercase code** (for example `eur`, `EURO`, `""`, a number): invalid,
  dead-lettered at once.
- **`refundedAt` or `occurredAt` without timezone, `version` other than 1**: invalid.
- **Order context exists but was recorded with a different customer than the one Orders knows**:
  not detectable here; the context's customer is used (first write wins, US-01).

## Requirements *(mandatory)*

### Functional Requirements

**Consuming and validating**

- **FR-001**: The service MUST consume `payments.payment-refunded` as defined in
  `ecommerce-infra/docs/events.md`, through the existing consumer and consumer group, as one more
  entry of the existing topic routing table; no second consuming mechanism may be introduced.
- **FR-002**: A refund is valid only if it is a JSON object with `eventId` (UUID), `aggregateId`
  (UUID, the payment id), `occurredAt` (ISO 8601 with timezone), `version` equal to `1`, `orderId`
  (UUID), `amount` (JSON number, finite, zero or greater), `currency` (three uppercase letters, ISO
  4217 form) and `refundedAt` (ISO 8601 with timezone). Unknown fields, including any `customerId`,
  MUST be ignored.
- **FR-003**: Invalid or unparseable refunds MUST NOT be partially processed; they are
  dead-lettered at once, without retries.

**Recording**

- **FR-004**: The order id of a refund MUST be taken from `orderId`; `aggregateId` MUST NOT be used
  as the order id.
- **FR-005**: The customer MUST be the customer of the order context for `orderId`. The service
  MUST NOT read the customer from the event.
- **FR-006**: For a valid refund with an order context, the service MUST store exactly one
  notification with a new unique id, the context's customer id, the order id, type
  `PAYMENT_REFUNDED`, channel `email`, status `SENT`, creation time (UTC), source event id
  (= `eventId`) and the message `Your payment of <amount> <currency> for order <orderId> was
  refunded.`, where `<amount>` has two decimals (rounded half up) and `<currency>` is the event's
  code.
- **FR-007**: The message MUST be deterministic and contain only ids, the amount and the currency.
- **FR-008**: After the notification is stored, the service MUST write the simulated send log line
  once, in the same format as the other notifications; never before the notification is stored and
  never for a duplicate.
- **FR-009**: The service MUST NOT create, change or delete order contexts while handling refunds.

**Missing order context**

- **FR-010**: If no order context exists for `orderId`, processing MUST fail with a typed,
  retryable error that names the order, and MUST NOT store a notification.
- **FR-011**: That failure MUST go through the existing retry with exponential backoff; once the
  attempts are exhausted the message MUST be dead-lettered with the attempts, the error type and
  message, logged with full context, and acknowledged.

**Reliability (unchanged mechanisms, applied to the new topic)**

- **FR-012**: At most one notification MUST exist per source event id; a redelivered refund stores
  nothing new and is not an error.
- **FR-013**: The offset MUST be committed only after the notification is stored (or recognised as
  already stored) or the message is dead-lettered.

**Configuration and documentation**

- **FR-014**: The topic name MUST be a setting with the contract name `payments.payment-refunded`
  as default, distinct from the other consumed topics, registered as one routing-table entry.
- **FR-015**: The README (consumed topics, configuration, processing guarantees), CLAUDE.md and
  `ecommerce-infra/docs/events.md` (Notifications consumer of `payments.payment-refunded` marked
  implemented) MUST be updated in the same change set.
- **FR-016**: The README MUST document the accepted limitation: a refund whose order context does
  not exist when its retries run out is dead-lettered, and is not recorded later even if the
  context arrives afterwards (for example while catching up on a backlog); such refunds are found
  in the dead-letter collection by their error type, and replay is not provided.

### Key Entities

- **Notification** (existing): gains the type `PAYMENT_REFUNDED`; same rules as before.
- **Order context** (existing, US-01): read-only here; supplies the customer of the order.
- **Refund** (inbound event, not stored): order id, payment id, amount, currency, refund time.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every valid refund whose order context exists, exactly one `PAYMENT_REFUNDED`
  notification exists for the right customer: 100% recorded, 0 duplicates after redelivery.
- **SC-002**: 0 notifications are ever stored without a customer or for a customer taken from
  anywhere but the order context.
- **SC-003**: Under normal conditions a refund is recorded within 5 seconds of being published.
- **SC-004**: No refund is lost: each ends as a notification or as a dead letter with its reason;
  a refund whose order context is missing ends in the dead-letter store after at most the
  configured retry budget (about 15 seconds with the defaults).
- **SC-005**: 100% of refund notifications show the refunded amount and currency.

## Assumptions

- **Contract**: `ecommerce-infra/docs/events.md` is the source of truth; checked against the
  producer (`ecommerce-payments`, `PaymentRefundedEvent`): camelCase fields, `amount` serialised as
  a JSON number, timestamps as ISO 8601 with offset, `version` 1.
- **Template**: `Your payment of <amount> <currency> for order <orderId> was refunded.` with two
  decimals for every currency (deterministic and consistent with US-01), even for currencies with
  other minor units.
- **Currency validation**: format only (three uppercase letters); the service does not keep a list
  of ISO 4217 codes.
- **Retry budget**: the existing `CONSUMER_RETRY_*` settings; no refund-specific retry settings.
- **Catch-up limitation**: during a backlog, a refund can be dead-lettered because its placement
  waits behind it on the same instance (Edge Cases). Accepted (clarification), documented in the
  README (FR-016); replay is out of scope.
- **Out of scope**: replaying dead-lettered messages once the context arrives late, any HTTP
  endpoint beyond health (US-04), real email sending.
