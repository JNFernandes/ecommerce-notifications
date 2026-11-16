# Data Model: Read-only notifications API (US-04)

No new collection and no domain change: the API reads the existing `notifications` collection
through the domain `Notification`.

## Application (`notifications.application`)

| Item | Kind | Fields / behaviour |
|---|---|---|
| `NotificationFilters` | frozen dataclass | `customer_id: UUID \| None`, `order_id: UUID \| None`, `type: NotificationType \| None` |
| `NotificationPage` | frozen dataclass | `items: tuple[Notification, ...]`, `total: int`, `limit: int`, `offset: int` |
| `NotificationReader` (port) | Protocol | `find_page(filters, limit, offset) -> list[Notification]` (newest first, `_id` tie-break); `count(filters) -> int`; `find_by_id(id) -> Notification \| None` |
| `NotificationNotFoundError(notification_id)` | exception | message `notification <id> not found` |
| `NotificationQueryService(reader)` | service | `list(filters, limit, offset) -> NotificationPage`; `get(id) -> Notification` (raises `NotificationNotFoundError`) |

## API (`notifications.api`): response models (separate from domain and documents)

`NotificationResponse` (frozen; camelCase on the wire):

| JSON field | Type | Description |
|---|---|---|
| `id` | UUID | Notification id |
| `customerId` | UUID | Customer the notification is for |
| `orderId` | UUID | Order the notification is about |
| `type` | `ORDER_RECEIVED` \| `ORDER_CONFIRMED` \| `ORDER_CANCELLED` \| `PAYMENT_REFUNDED` | What happened |
| `channel` | `email` | Delivery channel (simulated) |
| `message` | string | Text shown to the customer |
| `status` | `SENT` | Delivery status |
| `createdAt` | ISO 8601 UTC | When the notification was recorded |
| `sourceEventId` | UUID | The event that produced it |

`NotificationPageResponse`: `items: list[NotificationResponse]`, `total: int`, `limit: int`,
`offset: int`.

Errors use FastAPI's standard format. The 404 and 503 bodies are the frozen Pydantic model
`ErrorResponse(detail: str)` (constitution V: every HTTP response is a Pydantic model), giving
`{"detail": "notification <id> not found"}` and `{"detail": "database unavailable"}`. The 422 body
is FastAPI's own validation model: `{"detail": [ {loc, msg, type, …} ]}`.

## Persistence: indexes (`infrastructure/indexes.py`, `INDEX_SCHEMA_VERSION` 2 → 3)

| Name | Keys | Change |
|---|---|---|
| `uq_notifications_source_event_id` | `sourceEventId 1`, unique | unchanged |
| `ix_notifications_created_id` | `createdAt -1, _id -1` | new |
| `ix_notifications_customer_created_id` | `customerId 1, createdAt -1, _id -1` | new (replaces the next row) |
| `ix_notifications_customer_created` | `customerId 1, createdAt -1` | **obsolete, dropped at startup** |
| `ix_notifications_order_created_id` | `orderId 1, createdAt -1, _id -1` | new |
| `ix_notifications_type_created_id` | `type 1, createdAt -1, _id -1` | new |
| `uq_dead_letters_topic_partition_offset` | `topic 1, partition 1, offset 1`, unique | unchanged |

Query filter built from `NotificationFilters`: `{customerId: str(uuid)?, orderId: str(uuid)?,
type: value?}`. Sort: `[("createdAt", -1), ("_id", -1)]`.
