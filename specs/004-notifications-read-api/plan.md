# Implementation Plan: Read-only notifications API (US-04)

**Branch**: `feature/US-04-notifications-read-api` | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/004-notifications-read-api/spec.md`, plus the
planning input (router + query/response models, `NotificationQueryService`, read Protocol, typed
not-found error mapped in the API, Mongo paged query + count, new indexes, CORS from settings,
tests at every layer).

## Summary

Two `GET` endpoints over the existing `notifications` collection:
- `GET /notifications`, with combinable `customerId` / `orderId` / `type` filters and `limit` /
  `offset` paging. Results are newest first, with an `_id` tie-break, and come with an exact
  `total`.
- `GET /notifications/{id}`, with a 404 in FastAPI's standard format.

The router calls one `NotificationQueryService` method per request. The service depends on a
read-side `NotificationReader` Protocol, implemented by the existing Mongo repository.
`NotificationNotFoundError` and database errors are mapped to 404 and 503 by exception handlers
in the API layer.

Indexes move to version 3: each filter gets `(field, createdAt -1, _id -1)`, and the unfiltered
list gets `(createdAt -1, _id -1)`, so the sort never runs in memory. The old customer index is
dropped by the versioned module.

As constitution VII (v1.1.0) requires, every existing component test also reads its notification
back over HTTP.

## Technical Context

**Language/Version**: Python 3.12 (uv)

**Primary Dependencies**: FastAPI, Pydantic v2, PyMongo async API. No new dependency.

**Storage**: MongoDB `notifications` (read only for this story); indexes v2 → v3.

**Testing**: pytest, pytest-asyncio, httpx (`ASGITransport`), Testcontainers (`mongo:7`,
`confluentinc/cp-kafka:7.6.1`)

**Target Platform**: Linux container; local Windows development with uv

**Project Type**: Event-driven web service with a read-only HTTP API

**Performance Goals**: A filtered page from 10 000 notifications in under 300 ms (SC-002), backed
by index-only sorting, which `explain()` checks in the integration tests (research R2, R8).

**Constraints**:
- Read-only.
- Answers do not depend on Kafka.
- A database outage gives 503, never an empty page.
- No authentication (local and demo use only).

**Scale/Scope**: Thousands of notifications locally; offset paging is acceptable.

There is no `NEEDS CLARIFICATION`; see spec Clarifications and research R1–R8.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| # | Principle | How this plan complies | Status |
|---|---|---|---|
| I | Layers | The router does I/O and validation only, with one service call per request. `NotificationQueryService` orchestrates through the `NotificationReader` port. Response models are separate from the domain and documents; Mongo documents are mapped by `from_document`. Errors are mapped to HTTP in the API layer, never in the domain. | ✅ |
| II | Read-only API | Only `GET /notifications` and `GET /notifications/{id}`, the endpoints the constitution lists, plus health. No write endpoint, nothing published; other methods get 405. CORS origins come from an environment variable, empty by default. | ✅ |
| III | Write flow | Untouched; the read path performs no writes. | ✅ |
| IV | Notification integrity | Read-only views of immutable notifications; UTC ISO 8601 `createdAt`. | ✅ |
| V | Types & validation | Frozen Pydantic response models, including `ErrorResponse` for the 404 and 503 bodies (every HTTP response is a Pydantic model); FastAPI validates every input (422); enums for `type`, `channel` and `status`; typed `NotificationNotFoundError`; a narrow `PyMongoError` handler instead of a catch-all. | ✅ |
| VI | Test coverage | The query service has happy and edge tests; the API, repository and component layers are also tested. | ✅ |
| VII (v1.1.0) | Testing strategy | This story introduces the read API, so every existing component test that records a notification (order-placed, order-status, payment-refunded, dead-letters and Kafka-outage flows) also reads it back through `GET /notifications/{id}`, in this change set (FR-017). | ✅ |
| VIII | Kafka | Unchanged; the API works with Kafka down (tested). | ✅ |
| IX / X | Branch / gate | `feature/US-04-notifications-read-api`; four gate commands per task; every commit green. | ✅ |
| Stack / Docs | Indexes only through the versioned module (v3, with an obsolete index drop); OpenAPI descriptions on every endpoint, parameter and field; README: endpoints, indexes, `CORS_ALLOWED_ORIGINS`, a no-auth notice. | ✅ |

**Post-design re-check**: all ✅. Two points needed care:
- the old index must be dropped explicitly, or it stays forever (`ensure_indexes` only creates);
- 503 must come from a typed `PyMongoError` handler, not from catching `Exception`.

## Project Structure

### Documentation (this feature)

```text
specs/004-notifications-read-api/
├── spec.md, plan.md, research.md (R1–R8), data-model.md, quickstart.md
├── contracts/http-api.md
├── checklists/requirements.md
└── tasks.md   # /speckit-tasks
```

### Source Code (repository root)

```text
src/notifications/
├── main.py                                  # + NotificationQueryService on app.state
├── application/
│   ├── errors.py                            # + NotificationNotFoundError
│   ├── ports.py                             # + NotificationReader
│   └── query_notifications.py               # NEW NotificationFilters, NotificationPage, NotificationQueryService
├── api/
│   ├── app.py                               # + router, exception handlers
│   ├── errors.py                            # NEW 404 / 503 handlers
│   └── notifications.py                     # NEW router, NotificationResponse, NotificationPageResponse
└── infrastructure/
    ├── indexes.py                           # v3: 4 read indexes, OBSOLETE_INDEX_NAMES, drop in ensure_indexes
    └── repositories/notifications.py        # + find_page, count, find_by_id

tests/
├── support/fakes.py                         # + FakeNotificationReader
├── support/app.py                           # + assert_readable_over_http
├── unit/application/test_query_notifications.py
├── unit/api/test_notifications_endpoints.py
├── integration/test_notification_queries.py, test_indexes.py (v3)
└── component/test_notifications_api_flow.py (+ read-back in the existing component tests)
```

**Structure Decision**: The same single project. The API package gains its first business router;
the application package gains the read side.

### Request flow

```text
GET /notifications?…  → FastAPI validation (422) → router → NotificationQueryService.list
    → NotificationReader.find_page + count (Mongo: filter, sort createdAt/-_id, skip/limit; count_documents)
    → NotificationPage → NotificationPageResponse (200)
GET /notifications/{id} → validation (422) → router → NotificationQueryService.get
    → found: NotificationResponse (200) | NotificationNotFoundError → handler → 404
Any PyMongoError → handler → 503
```

## Complexity Tracking

No constitution violations to justify.
