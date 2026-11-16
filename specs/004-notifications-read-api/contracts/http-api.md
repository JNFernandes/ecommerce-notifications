# HTTP contract: notifications read API

This is read-only: only `GET` exists on these paths, and any other method gets 405. There is no
authentication (local and demo use only). The generated OpenAPI is at `/openapi.json`, with
interactive docs at `/docs`.

## `GET /notifications`

| Query parameter | Type | Default | Rule |
|---|---|---|---|
| `customerId` | UUID | — | optional filter |
| `orderId` | UUID | — | optional filter |
| `type` | `ORDER_RECEIVED` \| `ORDER_CONFIRMED` \| `ORDER_CANCELLED` \| `PAYMENT_REFUNDED` | — | optional filter |
| `limit` | integer | 20 | 1–100 |
| `offset` | integer | 0 | ≥ 0 |

Filters combine with AND. The order is `createdAt` descending, then `id` descending.

**200**:

```json
{
  "items": [
    {
      "id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
      "customerId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "orderId": "270bd352-0309-4356-9fa3-925a5450c361",
      "type": "PAYMENT_REFUNDED",
      "channel": "email",
      "message": "Your payment of 42.50 USD for order 270bd352-0309-4356-9fa3-925a5450c361 was refunded.",
      "status": "SENT",
      "createdAt": "2026-11-16T21:30:02.208Z",
      "sourceEventId": "6a1b2c3d-0000-4000-8000-000000000401"
    }
  ],
  "total": 3,
  "limit": 20,
  "offset": 0
}
```

An empty result is `{"items": [], "total": 0, "limit": 20, "offset": 0}`.

## `GET /notifications/{id}`

| Response | Body |
|---|---|
| **200** | one notification, the same shape as an `items` element |
| **404** | `{"detail": "notification <id> not found"}` |

## Errors (both endpoints)

| Status | When | Body |
|---|---|---|
| 422 | unknown `type`; `limit` or `offset` out of range or not an integer; a malformed UUID in the path or a filter | FastAPI standard: `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` |
| 503 | the database is unreachable | `{"detail": "database unavailable"}` |
| 405 | any method other than `GET` | FastAPI standard |

## CORS

Only the origins listed in `CORS_ALLOWED_ORIGINS` (comma separated) may read responses from a
browser, with `GET` only. The default is empty, meaning no cross-origin access.
