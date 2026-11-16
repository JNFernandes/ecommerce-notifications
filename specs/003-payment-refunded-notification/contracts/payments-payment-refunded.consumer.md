# Consumer contract: `payments.payment-refunded`

Source of truth: `ecommerce-infra/docs/events.md` → "Payments events /
`payments.payment-refunded`". The JSON shape was checked against the producer's
`PaymentRefundedEvent` (ecommerce-payments). This file states how Notifications **reads** the
event; the payload does not change.

- **Producer**: Payments, after a processed payment is refunded because its order was cancelled.
- **Consumer group**: `KAFKA_GROUP_ID` (shared with the order topics).
- **Topic name**: `KAFKA_TOPIC_PAYMENT_REFUNDED`, default `payments.payment-refunded`.
- **Key**: the payment id. It is ignored.
- **Value**: a UTF-8 JSON object.

## Fields used

| Field | JSON type | Required | Rule | Used for |
|---|---|---|---|---|
| `eventId` | string (UUID) | yes | valid UUID | `sourceEventId` (idempotency) |
| `aggregateId` | string (UUID) | yes | valid UUID | validated only (the **payment** id, never the order) |
| `occurredAt` | string (ISO 8601) | yes | with timezone | validated only |
| `version` | integer | yes | must be `1` | validated only |
| `orderId` | string (UUID) | yes | valid UUID | the order; key of the order context lookup |
| `amount` | number | yes | finite, `>= 0`, read exactly | message (two decimals) |
| `currency` | string | yes | three uppercase letters | message |
| `refundedAt` | string (ISO 8601) | yes | with timezone | validated only |
| `customerId` / anything else | any | no | ignored | — |

## Example

```json
{"eventId":"6a1b2c3d-0000-4000-8000-000000000401","occurredAt":"2026-11-16T10:05:00+00:00","aggregateId":"9a9a9a9a-0000-4000-8000-000000000401","version":1,"orderId":"0d1e2f3a-0000-4000-8000-000000000401","amount":129.90,"currency":"EUR","refundedAt":"2026-11-16T10:05:00+00:00"}
```

With an order context `0d1e2f3a-…0401 → customer C`, the result is a `PAYMENT_REFUNDED`
notification for `C` with the message
`Your payment of 129.90 EUR for order 0d1e2f3a-0000-4000-8000-000000000401 was refunded.`

## Outcomes

| Input | Stored | Retries | Dead letter | Offset committed |
|---|---|---|---|---|
| Valid, order context exists | the notification | on storage failure | only if all attempts fail | yes |
| Valid, no order context | nothing | yes (`CONSUMER_RETRY_*`) | yes, `errorType` `OrderContextNotFoundError` | yes, after the dead letter |
| Valid, context appears during the retries | the notification (next attempt) | stops | no | yes |
| Valid, redelivered | nothing | — | no | yes |
| Not JSON, a missing or invalid field, `version != 1`, a bad currency, a negative amount | nothing | no | yes, `attempts = 1` | yes |
