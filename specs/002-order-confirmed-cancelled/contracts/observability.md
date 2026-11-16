# Observability contract (changes in US-02)

US-01 log lines and readiness are unchanged (see
`specs/001-order-received-notification/contracts/observability.md`), except for the following.

| Event | Logger | Level | Message |
|---|---|---|---|
| Simulated send (all types) | `notifications.send` | `INFO` | `Simulated email sent: notification=<uuid> type=<ORDER_RECEIVED\|ORDER_CONFIRMED\|ORDER_CANCELLED> channel=email customer=<uuid> order=<uuid> sourceEvent=<uuid>` (unchanged format) |
| Duplicate event (all types) | `notifications.application` | `INFO` | `Event already recorded: type=<type> sourceEvent=<uuid> order=<uuid>`. **Replaces** US-01's `Order placed event already recorded: …`. |
| Message on a topic without a route | `notifications.messaging` | `ERROR` | The usual dead-letter line, with `errorType=InvalidMessageError error=no route for topic <topic>`. |

The cancellation reason appears only in the stored message. Log lines carry ids only.

Readiness (`GET /health/ready`) still reports a single `kafka` state, for the one consumer that
reads all three order topics.
