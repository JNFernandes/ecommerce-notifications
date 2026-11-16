# Data Model: Order received notification (US-01)

There are three layers, mapped explicitly (constitution Principle I):
- **Domain** objects: plain Python frozen dataclasses.
- **Inbound** event model: Pydantic, in `messaging`.
- **Persistence** documents: BSON, built only in `infrastructure`.

## Domain (`notifications.domain`)

### Enums (`StrEnum`)

| Enum | Values in this story | Later |
|---|---|---|
| `NotificationType` | `ORDER_RECEIVED` | `ORDER_CONFIRMED`, `ORDER_CANCELLED`, `PAYMENT_REFUNDED` (US-02, US-03) |
| `NotificationChannel` | `EMAIL` (`"email"`) | |
| `NotificationStatus` | `SENT` | |

### `Notification` (aggregate, immutable)

| Field | Type | Rules |
|---|---|---|
| `id` | `UUID` | New random UUID per notification. |
| `customer_id` | `UUID` | Required. |
| `order_id` | `UUID` | Required; `aggregateId` of the source event. |
| `type` | `NotificationType` | |
| `channel` | `NotificationChannel` | `EMAIL`. |
| `status` | `NotificationStatus` | `SENT`. |
| `message` | `str` | From the template; never empty. |
| `created_at` | `datetime` | UTC, timezone-aware, truncated to milliseconds. |
| `source_event_id` | `UUID` | `eventId` of the source event; unique across notifications. |

Factory: `Notification.order_received(*, notification_id, customer_id, order_id, total_amount,
source_event_id, now)` builds the `ORDER_RECEIVED` notification with the templated message.
- It raises `InvalidOrderTotalError` if `total_amount` is negative or not finite.
- It raises `NaiveTimestampError` if `now` is not timezone-aware.

No state transitions: a notification is created `SENT` and never changes (FR-008).

### `OrderContext` (aggregate, immutable)

| Field | Type | Rules |
|---|---|---|
| `order_id` | `UUID` | Identity; unique. |
| `customer_id` | `UUID` | |
| `total_amount` | `Decimal` | Exact value from the event; finite, `>= 0`. |
| `recorded_at` | `datetime` | UTC, timezone-aware, truncated to milliseconds. |

- `OrderContext.from_order_placed(*, order_id, customer_id, total_amount, now)` creates it.
- `conflicts_with(other) -> bool` is `True` when the customer or the total differ;
  `recorded_at` is ignored.
- The existing context always wins (FR-004): it is never replaced.

### Message template (`notifications.domain.messages`)

`order_received_message(order_id: UUID, total_amount: Decimal) -> str` returns
`f"Your order {order_id} for {amount} was received."`.

`amount` is `total_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`, formatted as a
fixed-point number: no exponent, no thousands separator, no currency. Examples:

| `totalAmount` | Shown as |
|---|---|
| `129.9` | `129.90` |
| `0` | `0.00` |
| `10.005` | `10.01` |
| `1E+2` | `100.00` |

### Errors (`notifications.domain.errors`)

`DomainError` (base) → `InvalidOrderTotalError`, `NaiveTimestampError`.

## Application (`notifications.application`)

- `OrderPlacedCommand`: frozen dataclass with `event_id`, `order_id`, `customer_id` and
  `total_amount`.
- `RecordOutcome` (`StrEnum`):
  - `RECORDED`: a new notification was stored and sent.
  - `ALREADY_RECORDED`: duplicate; nothing new was stored.
- `RecordOrderReceivedService.handle(command) -> RecordOutcome`. Steps:
  1. Build the `OrderContext` (domain).
  2. `order_contexts.add_if_absent(ctx)`. If it returns an existing context that
     `conflicts_with` the new one, log a WARNING.
  3. Build the `Notification` (domain).
  4. `notifications.add(notification)`. `True` means stored: call `sender.send(notification)`
     and return `RECORDED`. `False` means duplicate: return `ALREADY_RECORDED`, with no send.

Ports (`typing.Protocol`):

| Port | Method | Adapter |
|---|---|---|
| `NotificationRepository` | `async add(notification) -> bool` (`False` = duplicate source event) | `MongoNotificationRepository` |
| `OrderContextRepository` | `async add_if_absent(context) -> OrderContext \| None` (existing one, or `None` if inserted) | `MongoOrderContextRepository` |
| `NotificationSender` | `send(notification) -> None` | `LoggingNotificationSender` |
| `Clock` | `now() -> datetime` (UTC, aware) | `SystemClock` |
| `IdGenerator` | `new_id() -> UUID` | `Uuid4Generator` |

## Messaging (`notifications.messaging`)

### `OrderPlacedEvent` (Pydantic v2, frozen, `extra="ignore"`)

Field docstrings match `ecommerce-infra/docs/events.md`. JSON-type checks are described in
research R1.

| JSON field | Python | Required | Validation |
|---|---|---|---|
| `eventId` | `event_id: UUID` | yes | UUID string |
| `aggregateId` | `aggregate_id: UUID` | yes | UUID string; the order id |
| `occurredAt` | `occurred_at: AwareDatetime` | yes | ISO 8601 string with timezone |
| `version` | `version: Literal[1]` | yes | JSON integer `1` |
| `customerId` | `customer_id: UUID` | yes | UUID string |
| `totalAmount` | `total_amount: Decimal` | yes | JSON number, finite, `>= 0` |
| `items` | not modelled | no | ignored (unknown field) |

`to_command() -> OrderPlacedCommand`.

### `ConsumedMessage`

Frozen dataclass with `topic`, `partition`, `offset`, `key: bytes | None`, `value: bytes | None`
and `timestamp_ms`. It is built from the aiokafka record at the boundary, so aiokafka types stay
in one module.

### `DeadLetter` and the `DeadLetterStore` port

| Field | Type | Notes |
|---|---|---|
| `topic`, `partition`, `offset` | `str`, `int`, `int` | Unique together. |
| `key` | `str \| None` | UTF-8 text, or base64 if not valid UTF-8 (`key_encoding`). |
| `payload` | `str \| None` | Same rule (`payload_encoding`: `"utf-8"` or `"base64"`); `None` for a tombstone. |
| `error_type` | `str` | Exception class name, e.g. `InvalidMessageError`, `ServerSelectionTimeoutError`. |
| `error_message` | `str` | For validation: the list of invalid fields and reasons. |
| `attempts` | `int` | 1 for invalid messages; `max_attempts` after exhausted retries. |
| `first_failed_at` | `datetime` | UTC; time of the first failed attempt. |
| `dead_lettered_at` | `datetime` | UTC; time the record is stored. |

`DeadLetterStore.add(dead_letter) -> bool` returns `False` when the record already exists, which
counts as success.

### `RetryPolicy`

Frozen dataclass with `max_attempts`, `base_delay`, `multiplier` and `max_delay`; see research
R6. It is built from `Settings`.

## Persistence (`notifications.infrastructure`) — database `notifications`

UUIDs are stored as canonical lowercase strings, which are readable in tools and simple to filter
on in US-04. Amounts are stored as `Decimal128` and timestamps as BSON dates (UTC).

### Collection `notifications`

```json
{
  "_id": "6f1c…-uuid",
  "customerId": "c0ffee…-uuid",
  "orderId": "0rder…-uuid",
  "type": "ORDER_RECEIVED",
  "channel": "email",
  "status": "SENT",
  "message": "Your order 0rder…-uuid for 129.90 was received.",
  "createdAt": {"$date": "2026-11-16T10:00:00.123Z"},
  "sourceEventId": "e7e7…-uuid"
}
```

### Collection `order_context`

```json
{
  "_id": "0rder…-uuid",
  "customerId": "c0ffee…-uuid",
  "totalAmount": {"$numberDecimal": "129.90"},
  "recordedAt": {"$date": "2026-11-16T10:00:00.120Z"}
}
```

`_id` is the order id, which makes it unique by definition (FR-020: "unique order context by
order id").

### Collection `inbound_dead_letters`

```json
{
  "_id": {"$oid": "…"},
  "topic": "orders.order-placed",
  "partition": 1,
  "offset": 42,
  "key": "0rder…-uuid",
  "keyEncoding": "utf-8",
  "payload": "{\"eventId\": …}",
  "payloadEncoding": "utf-8",
  "errorType": "InvalidMessageError",
  "errorMessage": "customerId: Field required",
  "attempts": 1,
  "firstFailedAt": {"$date": "…"},
  "deadLetteredAt": {"$date": "…"}
}
```

### Indexes (`infrastructure/indexes.py`, `INDEX_SCHEMA_VERSION` 1 → 2)

| Collection | Name | Keys | Options |
|---|---|---|---|
| `notifications` | `uq_notifications_source_event_id` | `sourceEventId: 1` | unique |
| `notifications` | `ix_notifications_customer_created` | `customerId: 1, createdAt: -1` | |
| `inbound_dead_letters` | `uq_dead_letters_topic_partition_offset` | `topic: 1, partition: 1, offset: 1` | unique |

`order_context` needs no declared index, because `_id` is the order id and is unique.

## Relationships

- `Notification.order_id` → `OrderContext.order_id`: logical, not enforced. US-03 uses it to find
  the customer of a refund.
- `Notification.source_event_id` → the Kafka event `eventId`, at most one notification each.
- `DeadLetter` is independent: one per consumed message that could not be processed.
