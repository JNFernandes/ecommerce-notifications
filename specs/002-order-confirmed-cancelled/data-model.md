# Data Model: Order confirmed and order cancelled notifications (US-02)

Only the changes from US-01 are listed (see `specs/001-order-received-notification/data-model.md`).
No collection, document shape or index changes: `INDEX_SCHEMA_VERSION` stays 2.

## Domain (`notifications.domain`)

### `NotificationType` (extended)

| Value | Source topic | Story |
|---|---|---|
| `ORDER_RECEIVED` | `orders.order-placed` | US-01 |
| `ORDER_CONFIRMED` | `orders.order-confirmed` | **US-02 (new)** |
| `ORDER_CANCELLED` | `orders.order-cancelled` | **US-02 (new)** |

### `Notification` factories (new)

Both share the US-01 rules: new id, channel `EMAIL`, status `SENT`, `created_at` via
`require_utc_millis(now)`, immutable.

| Factory | Message |
|---|---|
| `Notification.order_confirmed(*, notification_id, customer_id, order_id, source_event_id, now)` | `order_confirmed_message(order_id)` |
| `Notification.order_cancelled(*, notification_id, customer_id, order_id, reason: str \| None, source_event_id, now)` | `order_cancelled_message(order_id, reason)` |

### Templates and reason normalisation (`notifications.domain.messages`)

| Function | Result |
|---|---|
| `order_confirmed_message(order_id)` | `Your order <order_id> was confirmed.` |
| `order_cancelled_message(order_id, reason)` | `Your order <order_id> was cancelled: <normalised reason>.`, or `Your order <order_id> was cancelled.` when `normalise_reason(reason)` is `None` |
| `normalise_reason(raw)` | See the rules below. |

`normalise_reason` rules, applied in order:

1. `None` → `None`.
2. Each run of `\r`, `\n` and `\t` becomes one space.
3. Trim.
4. Remove one trailing `.`, then trim again.
5. Empty → `None`.
6. More than 500 characters → the first 500 + `…`.

Constant: `MAX_REASON_LENGTH = 500`.

| Raw `reason` | Message suffix |
|---|---|
| absent, `null`, `""`, `"   "` | ` was cancelled.` |
| `"payment failed: amount exceeds the threshold"` | ` was cancelled: payment failed: amount exceeds the threshold.` |
| `"  stock rejected: insufficient stock.  "` | ` was cancelled: stock rejected: insufficient stock.` |
| `"line one\r\nline two"` | ` was cancelled: line one line two.` |
| 600 × `"x"` | ` was cancelled: ` + 500 × `x` + `….` |

## Application (`notifications.application`)

| Item | Kind | Notes |
|---|---|---|
| `recording.RecordOutcome` | `StrEnum` | Moved from `record_order_received`, which re-exports it (`RECORDED`, `ALREADY_RECORDED`). |
| `recording.record_and_send(notification, notifications, sender)` | async function | Runs `notifications.add`, then `sender.send` when stored, and returns the outcome. A duplicate logs INFO `Event already recorded: type=… sourceEvent=… order=…`. |
| `OrderConfirmedCommand` | frozen dataclass | `event_id`, `order_id`, `customer_id` |
| `OrderCancelledCommand` | frozen dataclass | `event_id`, `order_id`, `customer_id`, `reason: str \| None` (the raw value; normalised by the domain) |
| `RecordOrderStatusService(notifications, sender, clock, ids)` | service | `record_confirmed(cmd) -> RecordOutcome` and `record_cancelled(cmd) -> RecordOutcome`; no order context dependency (FR-009). |

## Messaging (`notifications.messaging`)

### `events.py` (new, shared)

- The JSON-type guards from US-01 research R1, moved here.
- `OrderEventEnvelope`: a frozen Pydantic model with `extra="ignore"` and
  `use_attribute_docstrings=True`.

  | JSON field | Python | Rule |
  |---|---|---|
  | `eventId` | `event_id: UUID` | JSON string, UUID |
  | `aggregateId` | `aggregate_id: UUID` | JSON string, UUID (the order id) |
  | `occurredAt` | `occurred_at: AwareDatetime` | JSON string, ISO 8601 with timezone |
  | `version` | `version: Literal[1]` | JSON integer 1 |
  | `customerId` | `customer_id: UUID` | JSON string, UUID |

- `parse_event(message, model) -> model`: decodes, validates, and maps `ValidationError` to
  `InvalidMessageError`.

### Event models

| Model | Extra fields | `to_command()` |
|---|---|---|
| `OrderPlacedEvent(OrderEventEnvelope)` | `totalAmount: Decimal` (unchanged rules) | `OrderPlacedCommand` |
| `OrderConfirmedEvent(OrderEventEnvelope)` | — | `OrderConfirmedCommand` |
| `OrderCancelledEvent(OrderEventEnvelope)` | `reason: str \| None = None`; before-validator: `None` or a JSON string, otherwise `ValueError("must be a JSON string or null")` | `OrderCancelledCommand` |

Parsers (one per topic, used by the routes): `parse_order_placed`, `parse_order_confirmed` and
`parse_order_cancelled`. Each returns the **command**, so routes connect parser output directly
to the service method input.

### `routing.py` (new)

| Item | Notes |
|---|---|
| `Route[C]` | frozen dataclass: `topic`, `parse: Callable[[ConsumedMessage], C]`, `handle: Callable[[C], Awaitable[object]]` |
| `RoutingTable(routes)` | rejects duplicate topics (`ValueError`); `topics: tuple[str, ...]`; `async handle(message)`: no route → `InvalidMessageError("no route for topic …")`, otherwise parse, then the route's service method once. Implements `MessageHandler`. |

The `MessageHandler` protocol becomes just `async handle(message)`; its `topic` attribute is
removed, because the manager takes the subscription from `routing.topics` and the processor never
read it. The manager passes the `RoutingTable` itself to `processor.process(message, routing)`, so
the processor is unchanged. Routes are held behind an `Any`-free protocol (`topic` +
`async run(message)`). `OrderPlacedHandler` is removed and replaced by its route.

## Configuration (`notifications.infrastructure.settings`)

| Field | Env var | Default | Validation |
|---|---|---|---|
| `kafka_topic_order_placed` | `KAFKA_TOPIC_ORDER_PLACED` | `orders.order-placed` | non-empty |
| `kafka_topic_order_confirmed` | `KAFKA_TOPIC_ORDER_CONFIRMED` | `orders.order-confirmed` | non-empty |
| `kafka_topic_order_cancelled` | `KAFKA_TOPIC_ORDER_CANCELLED` | `orders.order-cancelled` | non-empty |

The three topic names must be pairwise distinct (model validator).

## Persistence

Unchanged. The new types are stored as their string values in the existing `notifications`
collection, under the existing unique `sourceEventId` index.
