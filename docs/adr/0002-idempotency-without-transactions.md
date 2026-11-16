# 0002. Idempotency without transactions on a standalone MongoDB

- **Status**: Accepted
- **Date**: 2026-11-16

## Context

Kafka delivery is at-least-once. Every consumer must be idempotent by `eventId` and must commit
the offset only after the database save (constitution Principles III and VIII). A crash between
the save and the offset commit therefore redelivers an event whose effects are already stored.

Other services in the system solve this with a processed-events table written in the same
transaction as the state change. That is not available here: the shared MongoDB (ADR 0001) is a
standalone server, and MongoDB supports multi-document transactions only on replica sets and
sharded clusters.

One event can cause two writes: `orders.order-placed` stores an `OrderContext` and an
`ORDER_RECEIVED` notification.

## Decision

We will make every write idempotent on its own, so that the stored result is the same however
many times an event is processed and wherever a crash interrupts it:

- A `Notification` stores the `eventId` of the event that caused it as `sourceEventId`, under a
  **unique index**, and is written with an insert. A duplicate-key error on that index means "this
  event was already recorded": it is treated as success, not as a failure, and no second log line
  (simulated send) is written.
- An `OrderContext` is **upserted** by `orderId` (unique index on `orderId`). Writing it again with
  the same values changes nothing.
- When one event causes several writes, they are ordered so that a redelivery completes a partial
  result: for `orders.order-placed`, the `OrderContext` upsert comes before the notification
  insert.
- The offset is committed only after every write for the event has succeeded or been recognised as
  a duplicate.

We will not add a separate processed-events collection, and we will not convert the shared MongoDB
into a replica set for this service.

## Consequences

- Idempotency is enforced by the database, not only by application code: a bug cannot store two
  notifications for one event.
- No extra round trip to check a processed-events record before each write.
- A redelivered event is cheap: an upsert that changes nothing and a rejected insert.
- Each new kind of write must be idempotent by itself (a natural unique key or an upsert). This is
  reviewed against Principle III.
- Because notifications are immutable (Principle IV), there is no update that a duplicate could
  undo or reorder.
- The repository must map the duplicate-key error to "already recorded" narrowly, only for the
  `sourceEventId` index. Any other write error is a real failure and goes through the retry and
  dead-letter rules.
