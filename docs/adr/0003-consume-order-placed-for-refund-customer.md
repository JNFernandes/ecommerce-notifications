# 0003. Consume orders.order-placed to know the customer of a refund

- **Status**: Accepted
- **Date**: 2026-11-16

## Context

Notifications tells customers about their orders: received, confirmed, cancelled and refunded.
Every notification belongs to a customer (`customerId`), and a notification without a customer is
not allowed (constitution Principle IV).

The `orders.*` events carry `customerId`. `payments.payment-refunded` does not: its `aggregateId`
is the payment id, and it carries `orderId`, `amount`, `currency` and `refundedAt`
(`ecommerce-infra/docs/events.md`). Options considered:

1. Add `customerId` to `payments.payment-refunded`. This changes the Payments contract, and
   Payments would have to keep or look up a customer it does not otherwise need.
2. Call the Orders HTTP API from Notifications to resolve the customer. This adds a synchronous
   runtime dependency to a service whose input is events only (Principle II).
3. Consume `orders.order-placed` in Notifications and remember the customer and total of each
   order. This uses an event that already exists and is already published.

## Decision

We will consume `orders.order-placed` and store an `OrderContext` (`orderId`, `customerId`,
`totalAmount`, `recordedAt`), upserted by `orderId` (ADR 0002). The same event also records an
`ORDER_RECEIVED` notification. A `payments.payment-refunded` event resolves its customer through
the `OrderContext` of its `orderId`.

Topics are consumed independently, so a refund could be processed before the placement of its
order. A missing `OrderContext` is a **retryable failure**: the refund goes through the consumer's
exponential-backoff retries and, if the context still does not exist when the retries are
exhausted, into the inbound dead-letter collection. It is never skipped silently and never stored
without a customer.

## Consequences

- No contract change in Payments and no synchronous call to Orders.
- Notifications becomes a consumer of `orders.order-placed`; `ecommerce-infra/docs/events.md` must
  list it as one.
- Notifications keeps a small copy of order data, written only from the Orders event and never
  edited by hand.
- In practice the context exists long before any refund: a refund follows a cancellation, which
  follows the placement. The retry path covers a rare reordering, and the dead-letter collection
  makes a persistent gap visible instead of losing the notification.
- The `ORDER_RECEIVED` notification comes at no extra cost, because the event is consumed anyway.
