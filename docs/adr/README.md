# Architecture Decision Records

Architectural decisions for `ecommerce-notifications` are recorded here, as the project
constitution requires (Documentation Standards).

## Conventions

- One decision per file, named `NNNN-short-title.md` with a four-digit sequential number
  (`0001-...`, `0002-...`).
- Numbers are never reused or renumbered. To reverse a decision, write a new ADR and set the old
  one's status to `Superseded by NNNN`. The superseded ADR is kept.
- Start from [`template.md`](template.md) (Context / Decision / Consequences).
- Add every new ADR to the index below in the same PR.
- Adding any write endpoint or produced Kafka event (constitution Principle II), or a runtime
  dependency that duplicates an existing capability, requires an ADR before the PR can be merged.

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-mongodb-for-the-notification-log.md) | MongoDB for the notification log instead of PostgreSQL | Accepted |
| [0002](0002-idempotency-without-transactions.md) | Idempotency without transactions on a standalone MongoDB | Accepted |
| [0003](0003-consume-order-placed-for-refund-customer.md) | Consume orders.order-placed to know the customer of a refund | Accepted |
| [0004](0004-local-jwt-verification-with-jwks.md) | Verify access tokens locally against the Identity JWKS | Accepted |
| [0005](0005-show-ai-order-failure-explanations.md) | Show AI order failure explanations to the customer as untrusted, advisory text | Accepted |
