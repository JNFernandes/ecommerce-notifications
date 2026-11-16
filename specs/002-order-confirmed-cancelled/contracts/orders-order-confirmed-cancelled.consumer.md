# Consumer contract: `orders.order-confirmed` and `orders.order-cancelled`

Source of truth: `ecommerce-infra/docs/events.md` → "Orders events". This file states how
Notifications **reads** both events. Neither payload changes.

- **Producer**: Orders.
- **Consumer group**: `KAFKA_GROUP_ID`, the same group as `orders.order-placed`.
- **Key**: the order id. It is ignored.
- **Value**: a UTF-8 JSON object.
- **Topic names**: settings (`KAFKA_TOPIC_ORDER_CONFIRMED`, `KAFKA_TOPIC_ORDER_CANCELLED`),
  defaulting to the names above.

## Fields used (both topics)

| Field | JSON type | Required | Rule | Used for |
|---|---|---|---|---|
| `eventId` | string (UUID) | yes | valid UUID | `sourceEventId` (idempotency) |
| `aggregateId` | string (UUID) | yes | valid UUID | order id |
| `occurredAt` | string (ISO 8601) | yes | with timezone | validated only |
| `version` | integer | yes | must be `1` | validated only |
| `customerId` | string (UUID) | yes | valid UUID | notification customer |
| anything else | any | no | ignored | — |

## Additional field: `orders.order-cancelled`

| Field | JSON type | Required | Rule | Used for |
|---|---|---|---|---|
| `reason` | string or `null` | no | any other JSON type → invalid (dead letter) | message suffix, normalised (data-model.md) |

## Examples

Confirmation:

```json
{"eventId":"6a1b2c3d-0000-4000-8000-000000000201","aggregateId":"0d1e2f3a-0000-4000-8000-000000000201","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc"}
```

Result: `ORDER_CONFIRMED`, with the message
`Your order 0d1e2f3a-0000-4000-8000-000000000201 was confirmed.`

Cancellation with a reason:

```json
{"eventId":"6a1b2c3d-0000-4000-8000-000000000202","aggregateId":"0d1e2f3a-0000-4000-8000-000000000202","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc","reason":"payment failed: amount exceeds the threshold"}
```

Result: `ORDER_CANCELLED`, with the message
`Your order 0d1e2f3a-0000-4000-8000-000000000202 was cancelled: payment failed: amount exceeds the threshold.`

Cancellation without a reason (requested through the Orders API): the same payload without
`reason` gives `Your order <id> was cancelled.`

## Outcomes

These are the same as US-01 `orders.order-placed`, except that no order context is read or
written.

| Input | Stored | Retries | Dead letter | Offset committed |
|---|---|---|---|---|
| Valid, first delivery | the notification | on failure, up to `max_attempts` | only if all attempts fail | yes |
| Valid, redelivered | nothing | — | no | yes |
| Not UTF-8, not JSON, not an object, missing or invalid required field, `version != 1`, `reason` not a string or `null` | nothing | no | yes, `attempts = 1` | yes |
| Placement of the order never received | the notification (customer from the event) | — | no | yes |
