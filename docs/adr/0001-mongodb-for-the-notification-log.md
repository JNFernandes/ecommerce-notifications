# 0001. MongoDB for the notification log instead of PostgreSQL

- **Status**: Accepted
- **Date**: 2026-11-16

## Context

Notifications records one notification per relevant order event and serves them back through a
read-only API (constitution Principles II and IV). The data has these properties:

- It is an append-only log. A notification is immutable once stored; nothing is updated or
  deleted.
- Each notification is a self-contained document (ids, type, channel, message, status, timestamps)
  with no relations to join at read time.
- Reads are simple filters (`customerId`, `orderId`, `type`) ordered by `createdAt`, with
  limit/offset pagination.
- The service needs two small auxiliary collections: the `OrderContext` lookup (order id →
  customer, total) and the inbound dead-letter store, whose payloads are arbitrary JSON.
- No business invariant spans more than one document. The one rule that crosses events, "at most
  one notification per source event", can be enforced by a unique index.

The system follows database-per-service. Orders, Payments and Inventory use PostgreSQL for their
write-side invariants. The shared `ecommerce-infra` stack already runs a MongoDB 7 instance
(`localhost:27017`, also used by the Orders read model). It is a standalone server, not a replica
set.

## Decision

We will store all Notifications state in its own MongoDB database, `notifications`, on the shared
MongoDB instance, with the collections `notifications`, `order_context` and
`inbound_dead_letters`. The service uses the PyMongo async API (`AsyncMongoClient`). Indexes are
declared in the versioned module `notifications.infrastructure.indexes` and applied at startup.
No other service reads or writes this database.

We will not add a PostgreSQL database for Notifications.

## Consequences

- The document model matches the data: a notification and a dead-letter payload are stored as they
  are, without an ORM or a migration tool.
- No new infrastructure is needed; local setup and Testcontainers use one image (`mongo:7`).
- Filtered, newest-first queries are served by compound indexes declared in the index module.
- The shared instance is standalone, so multi-document transactions are not available. Every write
  has to be idempotent by itself; ADR 0002 records how.
- Schema discipline moves into code: domain objects are mapped explicitly to persistence documents
  (Principle I), and the index module is the only place where indexes change.
