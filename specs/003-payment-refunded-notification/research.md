# Research: Payment refunded notification (US-03)

Each entry: **Decision**, **Rationale**, **Alternatives considered**. Everything from US-01/US-02
research (lossless parsing, idempotent writes, consume loop, retries, dead letters, routing table)
is reused unchanged.

## R1. The event payload, as the producer writes it

**Decision**: Validate against the producer's actual JSON. `PaymentRefundedEvent` in
`ecommerce-payments` (Infrastructure/Messaging/IntegrationEvents) is serialised with the default
`System.Text.Json` options:

| JSON field | Producer type | JSON form |
|---|---|---|
| `eventId` | `Guid` | string UUID |
| `occurredAt` | `DateTimeOffset` | ISO 8601 with offset |
| `aggregateId` | `Guid` | string UUID (the **payment** id, also the message key) |
| `version` | `int` | integer `1` |
| `orderId` | `Guid` | string UUID |
| `amount` | `decimal` | JSON **number** |
| `currency` | `string` | `"EUR"`, … |
| `refundedAt` | `DateTimeOffset` | ISO 8601 with offset |

**Rationale**: `amount` arrives as a JSON number, so the existing lossless decoding
(`json.loads(..., parse_float=Decimal)`) and the `json_number` guard keep it exact. There is no
`customerId` field.

**Alternatives considered**: trusting only `events.md` ("decimal"), which would leave open whether
amounts are strings.

## R2. A generic envelope under the order envelope

**Decision**: Split `messaging/events.py`:
- `EventEnvelope` has `eventId`, `aggregateId`, `occurredAt` and `version`, with the guards and
  field docstrings already used.
- `OrderEventEnvelope(EventEnvelope)` adds `customerId`. No behaviour change for the order events.
- `PaymentRefundedEvent(EventEnvelope)` adds:
  - `orderId: JsonUuid`;
  - `amount: Decimal` (`json_number`, `>= 0`);
  - `currency: str` (`json_string`, pattern `^[A-Z]{3}$`);
  - `refundedAt: AwareDatetime` (`json_string`).

  `aggregate_id` keeps its generic docstring and is validated but never mapped to the order:
  `to_command()` uses `order_id` (FR-004).

**Rationale**: The constitution says the envelope (`eventId`, `occurredAt`, `aggregateId`,
`version`) is common to every event; `customerId` is only in `orders.*`. One copy of the guards
keeps the strictness identical for every topic.

**Alternatives considered**: a standalone refund model (duplicated envelope); making `customerId`
optional on the shared model (would weaken the order events' validation).

## R3. Reading the order context

**Decision**:
- Add `async get(order_id: UUID) -> OrderContext | None` to the `OrderContextRepository` port.
- `MongoOrderContextRepository.get` runs `find_one({"_id": str(order_id)})` and maps with the
  existing `from_document`.
- The in-memory fake gets the same method.
- No new index is needed: `_id` is the order id.

**Rationale**: It's the only new persistence capability. Reusing the US-01 mapping keeps
`Decimal128` and timestamps consistent.

## R4. Missing context is a typed, retryable application error

**Decision**:
- `application/errors.py` defines `OrderContextNotFoundError(Exception)`, with an `order_id`
  attribute and the message `no order context for order <order_id>`.
- `RecordPaymentRefundedService.handle(command)` raises it when `get` returns `None`, before
  building any notification.
- It is **not** an `InvalidMessageError`, so the existing `RetryingMessageProcessor` retries it
  with backoff and dead-letters it after `max_attempts`. The dead letter's `errorType` is
  `OrderContextNotFoundError` and its `errorMessage` names the order (FR-010, FR-011, FR-016).

**Rationale**: No change to the processor: the retry/dead-letter split already depends on
"invalid content" vs "anything else". Putting the error in `application` (not `domain`) matches
what it is: a missing piece of data found by the use case, not a broken business rule.

**Alternatives considered**:
- A special "retry longer" policy for this error (more configuration, and it doesn't fix the
  catch-up limitation, which comes from one message at a time per instance);
- skipping the refund (forbidden by the spec).

## R5. Domain: template, factory, amount validation

**Decision**: In `domain/messages.py`:
- `payment_refunded_message(order_id, amount, currency)` returns
  `Your payment of <amount> <currency> for order <order_id> was refunded.`
- The amount uses `format_amount` (two decimals, half up), and the currency is checked by
  `require_currency_code` (three uppercase letters, otherwise `InvalidCurrencyError`).
- New `InvalidAmountError(DomainError)` as a base; `InvalidOrderTotalError` becomes its subclass.
- `format_amount` raises `InvalidAmountError` for a negative or non-finite amount.
- `order_received_message` checks with `require_valid_total` first, so order totals still raise
  `InvalidOrderTotalError`.

`Notification.payment_refunded(*, notification_id, customer_id, order_id, amount, currency,
source_event_id, now)` goes through the shared `_create`.

**Rationale**: Refund amounts aren't order totals, so a generic amount error keeps the names
truthful without breaking any existing behaviour or test that expects `InvalidOrderTotalError` for
totals. The currency check in the domain backs up the parser (the same pattern as the totals).

**Alternatives considered**: reusing `InvalidOrderTotalError` for refunds (a misleading name).

## R6. Application service

**Decision**: `application/record_payment_refunded.py`:
- `PaymentRefundedCommand` (`event_id`, `order_id`, `amount`, `currency`);
- `RecordPaymentRefundedService(order_contexts, notifications, sender, clock, ids)`.

`handle(command)`:
1. `context = await order_contexts.get(command.order_id)`;
2. if there is none, raise `OrderContextNotFoundError`;
3. build `Notification.payment_refunded(customer_id=context.customer_id, …)`;
4. `record_and_send`.

It never writes order contexts (FR-009).

## R7. Settings, routing and test topics

**Decision**:
- **Setting**: `kafka_topic_payment_refunded`, default `payments.payment-refunded`, included in
  the distinct-topics validator.
- **Routing**: `build_routing_table` gets a fourth route,
  `(settings.kafka_topic_payment_refunded, parse_payment_refunded, record_payment_refunded.handle)`.
- **Tests**: in `tests/support/kafka.py`, `ORDER_TOPICS` is renamed to `CONSUMED_TOPICS` and gains
  `payments.payment-refunded`, and `create_order_topics` becomes `create_consumed_topics`. Every
  component fixture already calls it.

**Rationale**: US-02 T011 showed that a consumer subscribed to a topic the test broker hasn't
created stalls the group and breaks unrelated component tests. ecommerce-infra's `kafka-init`
already creates `payments.payment-refunded`, since Payments publishes it.

## R8. Tests

**Decision**:
- **Domain unit tests**:
  - template amounts `129.9` → `129.90`, `0`, `10.005` → `10.01`, a large exact value;
  - currencies `EUR` and `USD`, and the invalid `eur`, `EURO`, `""`;
  - negative / NaN amount → `InvalidAmountError`;
  - the factory's fields;
  - order totals still raise `InvalidOrderTotalError`.
- **Application unit tests**: context found → stored and sent with the context's customer; context
  missing → `OrderContextNotFoundError` naming the order, nothing stored or sent, no order context
  written; duplicate → not sent; a repository error propagates.
- **Messaging unit tests** (parser):
  - valid, with `aggregateId != orderId` → the command uses `orderId`;
  - `customerId` in the payload is ignored;
  - each required field missing;
  - `amount` as a string / bool / negative / int / large exact;
  - currency `eur` / `EURO` / `123`;
  - `refundedAt` naive;
  - bad JSON.
- **Envelope tests**: the order events are unchanged after the split.
- **Integration** (real MongoDB): `get` returns the stored context (exact `Decimal`, aware time)
  and `None` for an unknown order; a redelivered refund notification stays one document under the
  unique index.
- **Component** (real Kafka + MongoDB):
  - place then refund: produce `orders.order-placed`, wait for its context, produce the refund →
    one `PAYMENT_REFUNDED` notification for the placement's customer, with the exact message;
  - refund without context, with fast retries (`max_attempts` 3, base 0.05 s) → after the retries,
    one dead letter with `errorType` `OrderContextNotFoundError`, `attempts` 3 and the order in the
    message, the offset committed, and no notification;
  - the same refund twice → one notification, both offsets committed;
  - an invalid refund (missing `currency`) → a dead letter with `attempts` 1.

## R9. Documentation and contract

**Decision**:
- **README**: the consumed-topics row becomes implemented, with the message and the order context
  lookup; `KAFKA_TOPIC_PAYMENT_REFUNDED` goes in the configuration table; a "Known limitation:
  refund before its order context" note (FR-016) says how to find such refunds:
  `inbound_dead_letters` with `errorType: "OrderContextNotFoundError"`.
- **CLAUDE.md**: updated.
- **ecommerce-infra `docs/events.md`** (linked PR): the Notifications consumer of
  `payments.payment-refunded` becomes implemented, plus one sentence on how Notifications finds
  the customer.
