# Observability contract: logs and readiness

Log lines are plain text through the standard `logging` module, in the format configured by the
bootstrap: `%(asctime)s %(levelname)s %(name)s - %(message)s`. The fields below are part of the
message as `key=value` pairs, so they can be searched. Log lines contain ids, amounts and
technical context only; there is no personal data beyond `customerId` (Principle IV).

## Simulated send (FR-009)

- Logger `notifications.send`, level `INFO`, exactly once per stored notification, written after
  the insert succeeds:

  ```text
  Simulated email sent: notification=<uuid> type=ORDER_RECEIVED channel=email customer=<uuid> order=<uuid> sourceEvent=<uuid>
  ```

- It is never written for a duplicate.

## Processing

| Event | Logger | Level | Message (key fields) |
|---|---|---|---|
| Duplicate event | `notifications.application` | `INFO` | `Order placed event already recorded: sourceEvent=<uuid> order=<uuid>` |
| Order context conflict | `notifications.application` | `WARNING` | `Order context already recorded with different values; keeping the first: order=<uuid> existingCustomer=<uuid> eventCustomer=<uuid> existingTotal=<n> eventTotal=<n> sourceEvent=<uuid>` |
| Attempt failed, will retry | `notifications.messaging` | `WARNING` | `Processing failed, retrying: topic= partition= offset= attempt=<n>/<max> delay=<s> error=<type>: <msg>` |
| Dead-lettered | `notifications.messaging` | `ERROR` | `Message dead-lettered: topic= partition= offset= attempts= errorType= error=` (with the traceback for exhausted retries) |
| Already dead-lettered | `notifications.messaging` | `INFO` | `Message already dead-lettered: topic= partition= offset=` |
| Dead-letter store failed | `notifications.messaging` | `ERROR` | `Dead-letter store unavailable, holding message: topic= partition= offset= retryIn=<s> error=` |
| Commit failed | `notifications.messaging` | `WARNING` | `Offset commit failed; the message will be redelivered: topic= partition= offset= error=` |

## Readiness (unchanged contract)

`GET /health/ready` → `{"status", "mongodb", "kafka"}`:
- `kafka` is one of `STOPPED`, `CONNECTING`, `CONNECTED` or `DISCONNECTED`.
- `CONNECTED` now means "subscribed and consuming".
- The HTTP status depends on MongoDB only: 200 when it is up, 503 when it is down.
