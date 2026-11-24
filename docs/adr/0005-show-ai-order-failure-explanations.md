# 0005. Show AI order failure explanations to the customer as untrusted, advisory text

- **Status**: Accepted
- **Date**: 2026-11-24

## Context

The triage worker of `ecommerce-ai` publishes `ai.order-triage-completed` after it has explained
why an order failed (`ecommerce-infra/docs/events.md`). Each result has a `cause`, a plain-text
`explanation`, a `suggestedAction`, a `confidence`, a `promptVersion`, a `modelId` and a `source`
(`LLM` or `FALLBACK_TEMPLATE`). `aggregateId` is the order id, and the event has no `customerId`.

We want customers to see why their order failed. Three points make this different from every
other notification:

1. The text comes from a language model (or the worker's fallback template), not from a
   deterministic domain template. Principle IV required every message to come from a domain
   template with ids and amounts only.
2. The text is untrusted. It can contain control characters, bidirectional overrides, markup, or
   be longer than a notification should be, whatever the contract says.
3. The contract marks the result as advisory: no service may act on it, and `suggestedAction` is
   never a command.

## Decision

We will consume `ai.order-triage-completed` through the existing routing table and record one
`ORDER_FAILURE_EXPLANATION` notification per event, for the customer of the order context
recorded from `orders.order-placed` (as for refunds, ADR 0003). An unknown order is a retryable
`OrderContextNotFoundError` that ends in the inbound dead letters, never a silent skip.

- **The message is the explanation, cleaned by the domain** (`domain/messages.py::plain_text`):
  control and format characters (Unicode `Cc`, `Cf`, `Cs`, `Zl`, `Zp`) are removed, whitespace
  ones become a space, whitespace runs collapse, the text is trimmed and cut to 500 characters
  (ending with `…`). Text that is empty once cleaned is rejected. Same event, same text.
- **Plain text only.** The text is stored and returned as JSON strings, never as HTML, and is not
  HTML-escaped by us; clients must render it as text. Markup stays literal characters.
- **Advisory.** `suggestedAction`, `source`, `confidence` and `promptVersion` are stored on the
  notification (MongoDB sub-document `triage`) and returned as additional, nullable API fields.
  No code path reads `suggestedAction` to decide anything. `cause` and `modelId` are validated
  but not stored.
- **Strict contract.** Unknown `version`, `cause`, `suggestedAction` or `source`, a `confidence`
  outside 0 to 1, an `explanation` outside 1 to 1000 characters, or an `orderId` different from
  `aggregateId` makes the message invalid: it is dead-lettered at once.

## Consequences

- Customers see an explanation written by a model. Its wording is not reviewed per message; the
  contract requires ids only (no personal data), and we limit it to one line of 500 characters.
- Principle IV gets an explicit exception for this type (constitution 1.3.0).
- A failed order usually produces two triage results (the failed outcome and the cancellation),
  so the customer usually gets two explanations. Each is idempotent by its own `eventId`.
- Like refunds, a result processed before the order's placement is retried and then
  dead-lettered; it is not recorded later.
- `ecommerce-infra/docs/events.md` must say that Notifications records the result as a customer
  notification (the advisory paragraph currently lists "notification" among the things the event
  must not trigger) and move the topic to `implemented` with Notifications as a consumer.
