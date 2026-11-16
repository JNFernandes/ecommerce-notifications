# Consumer contract: `orders.order-placed`

Source of truth: `ecommerce-infra/docs/events.md` → "Orders events / `orders.order-placed`".
This file states how Notifications **reads** that event. It does not change the payload.

- **Producer**: Orders. **Consumer group**: `KAFKA_GROUP_ID` (default `notifications-service`).
- **Key**: the order id (`aggregateId`). It is ignored by Notifications.
- **Value**: a UTF-8 JSON object.

## Fields used

| Field | JSON type | Required | Rule | Used for |
|---|---|---|---|---|
| `eventId` | string (UUID) | yes | valid UUID | `sourceEventId` (idempotency) |
| `aggregateId` | string (UUID) | yes | valid UUID | order id |
| `occurredAt` | string (ISO 8601) | yes | must include a timezone | validated only |
| `version` | integer | yes | must be `1` | validated only |
| `customerId` | string (UUID) | yes | valid UUID | notification and order context |
| `totalAmount` | number | yes | finite, `>= 0`; read without loss of precision | message (2 decimals) and order context (exact) |
| `items` | array | no | not read | — |
| anything else | any | no | ignored | — |

## Example (valid)

```json
{
  "eventId": "6a1b2c3d-0000-4000-8000-000000000001",
  "aggregateId": "0d1e2f3a-0000-4000-8000-0000000000aa",
  "occurredAt": "2026-11-16T10:00:00.000Z",
  "version": 1,
  "customerId": "c1c2c3c4-0000-4000-8000-0000000000cc",
  "items": [{ "productId": "SKU-001", "quantity": 2, "unitPrice": 64.95 }],
  "totalAmount": 129.90
}
```

Result:
- order context `0d1e…aa → c1c2…cc, 129.90`
- notification `ORDER_RECEIVED` / `email` / `SENT`, with the message
  `Your order 0d1e2f3a-0000-4000-8000-0000000000aa for 129.90 was received.`

## Outcomes

| Input | Stored | Retries | Dead letter | Offset committed |
|---|---|---|---|---|
| Valid, first delivery | context (if absent) + notification | on failure, up to `max_attempts` | only if all attempts fail | yes, after the save or after the dead letter |
| Valid, redelivered | nothing new | — | no | yes |
| Not UTF-8, not JSON, not an object | nothing | no | yes, `attempts = 1` | yes |
| Missing or invalid required field, `version != 1`, negative total | nothing | no | yes, `attempts = 1` | yes |
| Dead-letter store unavailable | — | the whole message, until stored or dead-lettered | — | no, until then |
