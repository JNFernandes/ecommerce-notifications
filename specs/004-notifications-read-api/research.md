# Research: Read-only notifications API (US-04)

Each entry: **Decision**, **Rationale**, **Alternatives considered**.

## R1. Layering of the read path

**Decision**:
- `api/notifications.py`: a router with two endpoints. Query and path parameters use FastAPI
  `Query` / `Path` constraints, and `type` is typed with the domain `NotificationType` enum.
  Response models are `NotificationResponse` and `NotificationPageResponse`. Each request calls
  exactly one method of `NotificationQueryService`.
- `application/query_notifications.py`:
  - `NotificationFilters` (frozen dataclass: `customer_id`, `order_id`, `type`, all optional);
  - `NotificationPage` (`items`, `total`, `limit`, `offset`);
  - `NotificationQueryService(reader)` with `list(filters, limit, offset)` and `get(id)`, which
    raises `NotificationNotFoundError`.
- `application/ports.py`: a `NotificationReader` Protocol with `find_page(filters, limit,
  offset)`, `count(filters)` and `find_by_id(id)`. It is separate from the write-side
  `NotificationRepository`, and the Mongo adapter implements both.
- `application/errors.py`: `NotificationNotFoundError(notification_id)`.
- `api/errors.py`: `ErrorResponse(detail: str)`, a frozen Pydantic model, and the exception
  handlers. `NotificationNotFoundError` → 404 and `PyMongoError` → 503, both serialised from
  `ErrorResponse`, which keeps FastAPI's standard `{"detail": "..."}` shape and satisfies
  constitution V. Validation stays FastAPI's standard 422 (its own Pydantic model).

**Rationale**: Principle I. Routers do I/O only; the service orchestrates; errors are mapped to
HTTP in the API layer, never in the domain. The query service holds no business rules: listing
is not a business decision.

**Alternatives considered**:
- Calling the repository from the router (breaks Principle I).
- Mapping not-found inside the service as an HTTP error (leaks HTTP into the application).

## R2. Sort, tie-break and the indexes that serve them

**Decision**: Sort by `createdAt` descending, then `_id` descending; `skip(offset).limit(limit)`.
Indexes, in the versioned module (`INDEX_SCHEMA_VERSION` 2 → 3):

| Name | Keys | Serves |
|---|---|---|
| `ix_notifications_created_id` (new) | `createdAt -1, _id -1` | the unfiltered list |
| `ix_notifications_customer_created_id` (replaces `ix_notifications_customer_created`) | `customerId 1, createdAt -1, _id -1` | the `customerId` filter |
| `ix_notifications_order_created_id` (new) | `orderId 1, createdAt -1, _id -1` | the `orderId` filter |
| `ix_notifications_type_created_id` (new) | `type 1, createdAt -1, _id -1` | the `type` filter |

A new tuple, `OBSOLETE_INDEX_NAMES`, lists `("notifications", "ix_notifications_customer_created")`.
`ensure_indexes` drops each obsolete index if it exists (idempotent), then creates the current
ones.

**Rationale**: With the `_id` tie-break in the sort, an index on `(field, createdAt)` alone still
needs an in-memory SORT stage. Including `_id` lets MongoDB walk the index in order: no blocking
sort, stable paging. Combined filters use one of these indexes plus a residual filter, which is
fine at this scale. Dropping the old index keeps the index set exactly as declared (Technology
Stack: index changes only through the versioned module).

**Alternatives considered**:
- Keeping the old customer index and adding a new one (two overlapping indexes, more write cost);
- one compound index for every filter combination (too many);
- cursor pagination (out of scope).

## R3. Counting

**Decision**: `count_documents(filter)` with the same filter as the page query (clarified). It runs
as a separate query, so the page and the count can disagree briefly while notifications are being
written. That's acceptable.

## R4. Validation and status codes

**Decision**:
- `limit: int = Query(20, ge=1, le=100)`, `offset: int = Query(0, ge=0)`;
- `customerId`, `orderId` and the path `id` are typed `UUID`, so FastAPI returns 422 for malformed
  values. Uppercase UUIDs are accepted and normalised, since the stored form is the lowercase
  string;
- `type: NotificationType | None`, so an unknown value is a 422 listing the allowed values;
- the query parameter names are camelCase (`customerId`, `orderId`) through `alias=`, to match the
  JSON fields;
- unknown query parameters are ignored (FastAPI default);
- write methods get 405 from Starlette, because only `GET` routes exist on these paths;
- a database outage raises `PyMongoError` (server selection timeout, 3 s), which the 503 handler
  catches. A dedicated handler is safer than a catch-all `Exception` (Principle V).

## R5. Response representation

**Decision**:
- `NotificationResponse` is a frozen Pydantic model with camelCase aliases (`serialize_by_alias`)
  and a `description` on every field: `id`, `customerId`, `orderId` and `sourceEventId` (UUID),
  `type` (enum), `channel` (enum), `message`, `status` (enum) and `createdAt` (datetime). It is
  built from the domain `Notification` by `NotificationResponse.from_domain`.
- `createdAt` is serialised as ISO 8601 in UTC (`…Z`).
- `NotificationPageResponse` has `items`, `total`, `limit` and `offset`, with descriptions.
- Both endpoints document their 404 / 422 / 503 responses in `responses=`.

**Rationale**: FR-008. The response model is separate from the domain dataclass and from the Mongo
document, and it is mapped explicitly.

## R6. Repository implementation

**Decision**: `MongoNotificationRepository` gets `find_page`, `count` and `find_by_id`.
- One private `_query(filters)` builds `{customerId?: str, orderId?: str, type?: value}`.
- Results are mapped with the existing `from_document`, so `createdAt` stays timezone-aware UTC
  because the client uses `tz_aware=True`.

## R7. Wiring and CORS

**Decision**:
- `main.py` builds `NotificationQueryService(MongoNotificationRepository(database))` and puts it on
  `app.state.notification_query_service`. The router reads it through a dependency, mirroring
  the health router.
- `build_api` registers the router and the exception handlers.
- CORS is unchanged: `CORSMiddleware` with the origins from `settings.cors_allowed_origins`
  (`CORS_ALLOWED_ORIGINS`, empty by default) and `GET` only. It already applies to every route,
  including the new ones. The README gets the variable with an example frontend origin.

## R8. Tests

**Decision**:
- **Unit, application:** the query service with a fake reader. A list passes the filters, limit
  and offset through and returns a page with the total. `get` covers found and missing →
  `NotificationNotFoundError`.
- **Unit, API** (httpx + `ASGITransport`, a fake service on `app.state`):
  - the 200 shape and camelCase fields;
  - the defaults `limit` 20 / `offset` 0;
  - `limit` 0 / 101, `offset` -1 and `limit=abc` → 422;
  - `type=FOO` → 422 naming the allowed values;
  - a bad UUID in the path and in the filters → 422;
  - an uppercase UUID accepted;
  - not found → 404 `{"detail": "notification <id> not found"}`;
  - a reader raising `ServerSelectionTimeoutError` → 503;
  - `POST`/`PUT`/`PATCH`/`DELETE` → 405;
  - CORS headers for an allowed origin, none for an unlisted one;
  - the OpenAPI document has a description on every parameter and response field.
- **Integration** (real MongoDB):
  - each filter and combinations of them;
  - newest first, with identical timestamps ordered by `_id` descending;
  - walking all pages returns each notification exactly once;
  - an offset past the end → empty page with the real total;
  - `total` with filters;
  - `find_by_id` found / missing;
  - indexes: version 3, the obsolete index dropped, and `explain()` for each single filter shows an
    index scan with **no SORT stage**. This is a deterministic stand-in for the latency target
    (SC-002).
- **Component** (real Kafka + MongoDB): produce an event, then read it through
  `GET /notifications?orderId=…` and `GET /notifications/{id}`. FR-017: every existing component
  test that records a notification also reads it back through `GET /notifications/{id}`, using a
  shared helper `assert_readable_over_http(app, document)` in `tests/support/app.py`.
