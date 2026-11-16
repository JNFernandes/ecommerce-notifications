# Data Model: Payment refunded notification (US-03)

Only the changes from US-01/US-02 are listed. No collection, document shape or index changes:
`INDEX_SCHEMA_VERSION` stays 2.

## Domain (`notifications.domain`)

### `NotificationType` (extended)

| Value | Source topic | Story |
|---|---|---|
| `PAYMENT_REFUNDED` | `payments.payment-refunded` | **US-03 (new)** |

### Errors (`domain/errors.py`)

```text
DomainError
├── InvalidAmountError          (new) negative or non-finite amount
│   └── InvalidOrderTotalError  (now a subclass; unchanged meaning for order totals)
├── InvalidCurrencyError        (new) not three uppercase letters
└── NaiveTimestampError
```

### Template (`domain/messages.py`)

| Function | Result |
|---|---|
| `require_currency_code(code)` | `code` if it matches `^[A-Z]{3}$`, else `InvalidCurrencyError` |
| `payment_refunded_message(order_id, amount, currency)` | `Your payment of <format_amount(amount)> <currency> for order <order_id> was refunded.` |

`format_amount` now raises `InvalidAmountError`. `order_received_message` validates with
`require_valid_total` first (still `InvalidOrderTotalError`).

| amount | currency | Shown |
|---|---|---|
| `129.9` | `EUR` | `129.90 EUR` |
| `0` | `USD` | `0.00 USD` |
| `10.005` | `EUR` | `10.01 EUR` |
| `123456789012345678.123456789` | `EUR` | `123456789012345678.12 EUR` |

### Factory

`Notification.payment_refunded(*, notification_id, customer_id, order_id, amount, currency,
source_event_id, now)` goes through `_create`, with type `PAYMENT_REFUNDED`, channel `email` and
status `SENT`.

## Application (`notifications.application`)

| Item | Kind | Notes |
|---|---|---|
| `ports.OrderContextRepository.get(order_id)` | port method (new) | `OrderContext \| None` |
| `errors.OrderContextNotFoundError(order_id)` | exception (new) | message `no order context for order <order_id>`; retryable (not `InvalidMessageError`) |
| `PaymentRefundedCommand` | frozen dataclass | `event_id`, `order_id` (from `orderId`), `amount: Decimal`, `currency: str` |
| `RecordPaymentRefundedService(order_contexts, notifications, sender, clock, ids)` | service | `handle(command) -> RecordOutcome`: `get` → missing: raise; found: `Notification.payment_refunded(customer_id=context.customer_id, …)` → `record_and_send`. Never writes order contexts. |

## Messaging (`notifications.messaging`)

### `events.py` (split)

| Model | Fields |
|---|---|
| `EventEnvelope` (new base) | `eventId`, `aggregateId`, `occurredAt`, `version`: the rules are unchanged |
| `OrderEventEnvelope(EventEnvelope)` | + `customerId` (unchanged behaviour) |

### `payment_refunded.py` (new)

`PaymentRefundedEvent(EventEnvelope)`:

| JSON field | Python | Rule |
|---|---|---|
| `aggregateId` | `aggregate_id: UUID` | the **payment** id; validated, never used as the order |
| `orderId` | `order_id: UUID` | JSON string, UUID; **the order** |
| `amount` | `amount: Decimal` | JSON number, finite, `>= 0`, exact |
| `currency` | `currency: str` | JSON string, `^[A-Z]{3}$` |
| `refundedAt` | `refunded_at: AwareDatetime` | JSON string, ISO 8601 with timezone; validated only |
| `customerId`, anything else | — | ignored |

`parse_payment_refunded(message) -> PaymentRefundedCommand`.

## Persistence

- `MongoOrderContextRepository.get(order_id)` runs `find_one({"_id": str(order_id)})`, then
  `from_document`. This is read-only.
- Refund notifications go into the existing `notifications` collection under the unique
  `sourceEventId` index.

## Configuration

| Field | Env var | Default | Validation |
|---|---|---|---|
| `kafka_topic_payment_refunded` | `KAFKA_TOPIC_PAYMENT_REFUNDED` | `payments.payment-refunded` | non-empty; distinct from the three order topics |

## Dead letter for a missing context (existing shape)

```json
{
  "topic": "payments.payment-refunded",
  "errorType": "OrderContextNotFoundError",
  "errorMessage": "no order context for order 0d1e2f3a-…",
  "attempts": 5
}
```

The other fields are as in US-01.
