---

description: "Task list for US-04 read-only notifications API"
---

# Tasks: Read-only notifications API (US-04)

**Input**: Design documents from `specs/004-notifications-read-api/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R8), data-model.md, contracts/http-api.md,
quickstart.md

**Tests**: Required (constitution VI and VII v1.1.0; requested in the planning input).

**Gate (every task)**: each task ends with `uv run ruff check` → `uv run ruff format --check` →
`uv run mypy` → `uv run pytest --cov`, all green. Write the test first and see it fail, then
implement. **One task = one commit** (`feat(US-04)`, `test(US-04)`, `docs(US-04)`). Never commit a
red gate.

**Order** (planning input): query service → repository paging and indexes → router and response
models → CORS → component tests → docs.

**Story labels**: `[US1]` list, `[US2]` fetch one, `[US3]` bad requests rejected, `[US4]`
documented, CORS, works without Kafka. They are inside the single backlog item US-04.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Foundational (application and persistence)

**Purpose**: The read side below the API, used by every story.

**⚠️ CRITICAL**: No user story work starts before this phase is complete.

- [X] T001 [P] Query service, through the port, the fake and its tests:
  - in `src/notifications/application/ports.py`, add the `NotificationReader` Protocol:
    `find_page(filters, limit, offset) -> list[Notification]` (newest first, `_id` tie-break),
    `count(filters) -> int`, `find_by_id(notification_id) -> Notification | None`;
  - in `src/notifications/application/errors.py`, add
    `NotificationNotFoundError(notification_id)` with the message
    `notification <id> not found` and a `notification_id` attribute;
  - create `src/notifications/application/query_notifications.py` with:
    - `NotificationFilters`, a frozen dataclass: `customer_id`, `order_id` and `type`, all
      optional;
    - `NotificationPage`, a frozen dataclass: `items: tuple[Notification, ...]`, `total`,
      `limit`, `offset`;
    - `NotificationQueryService(reader)`, where `list(filters, limit, offset)` calls `find_page`
      and `count` and returns the page, and `get(notification_id)` returns the notification or
      raises `NotificationNotFoundError`.

  `FakeNotificationReader` goes in `tests/support/fakes.py`. It is in-memory, sorts like the
  repository, records the calls it receives, and can raise a given error.

  Tests in `tests/unit/application/test_query_notifications.py`:
  - list passes the filters, limit and offset through and returns items plus total;
  - an empty result gives `total` 0;
  - `get` found;
  - `get` missing → `NotificationNotFoundError` naming the id;
  - a reader error propagates. Gate.
- [X] T002 [P] Indexes v3, in `src/notifications/infrastructure/indexes.py`:
  - `INDEX_SCHEMA_VERSION = 3`, with the history line "3: US-04 read indexes…";
  - replace `NOTIFICATIONS_CUSTOMER_CREATED_INDEX` with `ix_notifications_customer_created_id`
    (`customerId 1, createdAt -1, _id -1`);
  - add `ix_notifications_created_id` (`createdAt -1, _id -1`),
    `ix_notifications_order_created_id` (`orderId 1, …`) and `ix_notifications_type_created_id`
    (`type 1, …`);
  - add `OBSOLETE_INDEX_NAMES: tuple[tuple[str, str], ...] = (("notifications",
    "ix_notifications_customer_created"),)`. `ensure_indexes` first drops each obsolete index that
    exists (check `index_information()`; never fail when it is absent), then creates the current
    ones. The log line includes the dropped count.

  Integration tests in `tests/integration/test_indexes.py`:
  - version 3 and the exact key patterns of the four read indexes;
  - after creating the old index by hand, `ensure_indexes` removes it;
  - running `ensure_indexes` twice is a no-op;
  - the existing unique indexes are unchanged. Gate.
- [X] T003 Repository paging (depends on T001, T002). In
  `src/notifications/infrastructure/repositories/notifications.py`, add to
  `MongoNotificationRepository`:
  - `find_page(filters, limit, offset)`: filter from the given fields (UUIDs as strings, type as
    its value), `sort([("createdAt", -1), ("_id", -1)])`, `skip(offset)`, `limit(limit)`, mapped
    with `from_document`;
  - `count(filters)`: `count_documents` with the same filter;
  - `find_by_id(notification_id)`.

  One private `_query(filters)` builds the filter for both.

  Integration tests in the new `tests/integration/test_notification_queries.py`, with
  `ensure_indexes` and a seeded mix of customers, orders, types and timestamps, including three
  with the same `createdAt`:
  - each single filter and two combinations;
  - newest-first order, with equal timestamps ordered by id descending;
  - walking every page with `limit` 3 returns each notification exactly once;
  - an offset past the end → `[]`, and `count` unchanged;
  - `count` with filters;
  - `find_by_id` found and missing;
  - for no filter and for each single filter, the winning plan from `explain()` uses an `IXSCAN`
    and contains no `SORT` stage (SC-002). Gate.

**Checkpoint**: The read side works against real MongoDB; nothing is exposed yet.

---

## Phase 2: User Story 1 — A client lists notifications, newest first (Priority: P1) 🎯 MVP

**Goal**: `GET /notifications` with filters, paging and `total`.

**Independent Test**: With a fake service, the API answers the documented page shape; with
filters, the service receives them.

- [X] T004 [US1] Router, response models, wiring (depends on T001, T003):
  - create `src/notifications/api/notifications.py` with:
    - `NotificationResponse` (frozen; camelCase aliases; a description on every field;
      `from_domain(notification)`);
    - `NotificationPageResponse` (`items`, `total`, `limit`, `offset`, with descriptions);
    - a `get_notification_query_service(request)` dependency reading
      `app.state.notification_query_service` (an `isinstance` check, as in `health.py`);
    - `GET /notifications` (tag `notifications`, summary and description) with `customerId` and
      `orderId` (`UUID | None`, `alias=`), `type: NotificationType | None`,
      `limit: int = Query(20, ge=1, le=100)` and `offset: int = Query(0, ge=0)`, each with a
      description. It calls `service.list(...)` once.
  - create `src/notifications/api/errors.py` with `ErrorResponse`, a frozen Pydantic model
    (`detail: str`, with a description) that keeps FastAPI's standard `{"detail": ...}` shape while
    satisfying constitution V (every HTTP response is a Pydantic model); a `PyMongoError` → 503
    handler returning `ErrorResponse(detail="database unavailable")` (logs at WARNING); and
    `register_error_handlers(app)`. `GET /notifications` documents `503: {"model": ErrorResponse}`;
  - `src/notifications/api/app.py`: include the router and register the handlers;
  - `src/notifications/main.py`: put
    `NotificationQueryService(MongoNotificationRepository(database))` on
    `app.state.notification_query_service`.

  Tests:
  - `tests/unit/api/test_notifications_endpoints.py`, using httpx `ASGITransport` with a
    `FakeNotificationReader` behind a real `NotificationQueryService`:
    - the 200 page shape with camelCase fields and `createdAt` in ISO 8601 UTC;
    - the defaults `limit` 20 / `offset` 0;
    - the filters reach the reader;
    - an empty result → `{"items": [], "total": 0, …}`;
    - the reader raising `ServerSelectionTimeoutError` → 503 with the body above;
  - update `tests/component/test_service_startup.py`: the expected OpenAPI paths become
    `{"/health/live", "/health/ready", "/notifications"}` (T005 adds
    `/notifications/{notification_id}`). Gate.

**Checkpoint**: Listing works through the API (unit level).

---

## Phase 3: User Story 2 — A client fetches one notification (Priority: P1)

**Goal**: `GET /notifications/{id}` with a 404.

**Independent Test**: Fetching an existing id gives 200 with all fields; a well-formed unknown id
gives 404 `{"detail": "notification <id> not found"}`.

- [ ] T005 [US2] In `src/notifications/api/notifications.py`, add
  `GET /notifications/{notification_id}` (`Path` with a description; `responses` documents 404,
  422 and 503), which calls `service.get(...)` once. In `src/notifications/api/errors.py`, add the
  `NotificationNotFoundError` → 404 handler returning `ErrorResponse(detail=str(error))`. The
  endpoint documents `404` and `503` with `{"model": ErrorResponse}`.

  Tests in `tests/unit/api/test_notifications_endpoints.py`: found → 200 equal to the stored
  notification; unknown → 404 with the exact body. In `tests/component/test_service_startup.py`,
  add the `{notification_id}` path to the OpenAPI set. Gate.

---

## Phase 4: User Story 3 — Bad requests are rejected clearly (Priority: P2)

**Goal**: 422 for bad input, never 5xx; 405 for write methods.

**Independent Test**: Every invalid request in the matrix below gives 422 (or 405), and none
gives 5xx.

- [ ] T006 [US3] Tests in `tests/unit/api/test_notifications_endpoints.py`, expected to pass with
  no production change (if one is needed, keep it inside the API package):
  - `limit` 0 / 101 / `abc` → 422 naming `limit`;
  - `offset` -1 → 422;
  - `type=FOO` → 422 whose details list the four allowed values;
  - `customerId=not-a-uuid`, `orderId=123` and path `not-a-uuid` → 422 naming the field;
  - an uppercase UUID in the path and in a filter is accepted, with the lowercase value reaching
    the reader;
  - `POST`, `PUT`, `PATCH` and `DELETE` on `/notifications` and `/notifications/{id}` → 405;
  - an unknown query parameter is ignored → 200. Gate.

---

## Phase 5: User Story 4 — Documented, CORS, works without Kafka (Priority: P3)

**Goal**: A complete OpenAPI document, cross-origin access from configuration, and reads that
don't depend on Kafka.

**Independent Test**: OpenAPI descriptions everywhere; CORS headers only for configured origins;
reads work with the broker paused.

- [ ] T007 [P] [US4] OpenAPI test in `tests/unit/api/test_notifications_endpoints.py`. For both
  notification operations, every parameter has a non-empty `description`. Every property of the
  `NotificationResponse` and `NotificationPageResponse` schemas has a `description`. The 404, 422
  and 503 responses are documented on `GET /notifications/{notification_id}`, and 422 and 503 on
  `GET /notifications`. Gate.
- [ ] T008 [P] [US4] CORS tests in the new `tests/unit/api/test_notifications_cors.py`:
  - with `cors_allowed_origins=["http://localhost:5173"]`, a `GET /notifications` with that
    `Origin` gets `access-control-allow-origin: http://localhost:5173`;
  - `Origin: http://evil.example` gets no such header;
  - with an empty list (the default), no header at all;
  - a preflight `OPTIONS` asking for `DELETE` is not allowed.

  The production code is expected to stay as it is (`build_api` already configures CORS). Gate.
- [ ] T009 [US4] Component tests in the new `tests/component/test_notifications_api_flow.py`, with
  the real lifespan, MongoDB and Kafka containers (`create_consumed_topics`, `run_app`):
  - produce an `orders.order-placed` event, then `eventually` `GET /notifications?orderId=<id>`
    returns it (`total` 1, `type` `ORDER_RECEIVED`), and `GET /notifications/{id}` returns the same
    object;
  - pause Kafka (`pause_kafka`, `unpause` in `finally`) → `GET /notifications?orderId=<id>` still
    answers 200 with the same content (SC-005). Gate.

---

## Phase 6: Constitution VII catch-up and polish

- [ ] T010 FR-017 / constitution VII (v1.1.0). In `tests/support/app.py`, add
  `assert_readable_over_http(app, document)`. It runs `GET /notifications/{document["_id"]}` and
  asserts 200, with a body equal to the stored document mapped to the API shape (`id` = `_id`,
  every other field identical, and `createdAt` the same instant).

  Call it for every notification asserted in:
  - `tests/component/test_order_placed_flow.py`
  - `tests/component/test_order_status_flow.py`
  - `tests/component/test_payment_refunded_flow.py`
  - `tests/component/test_dead_letters_flow.py` (the valid message's notification)
  - `tests/component/test_kafka_outage.py` (the notifications recorded after each outage)

  Gate.
- [ ] T011 [P] Update `README.md`:
  - status line;
  - the HTTP API table: both endpoints available, with parameters, responses and examples from
    `contracts/http-api.md`;
  - an "Indexes" subsection listing all v3 indexes and what each serves;
  - `CORS_ALLOWED_ORIGINS` with an example frontend origin;
  - a notice: **no authentication, for local and demo use only**. Gate.
- [ ] T012 [P] Update `CLAUDE.md`:
  - make the "Active feature" notes permanent rules (read path layers; 404/503 handlers in
    `api/errors.py`; indexes v3 with `OBSOLETE_INDEX_NAMES`; the read-back helper for every new
    component test);
  - change the "Component tests and the read API" rule to say the read API now exists and every
    component test must use `assert_readable_over_http`;
  - extend "Where things are" with US-04;
  - remove "Active feature". Gate.
- [ ] T013 Run quickstart.md steps 2–5 against ecommerce-infra on a free port (port 8000 may be
  taken by your own instance). Use a throwaway database and consumer group, and clean them up.
  Record any deviation as a fix. Gate.

---

## Dependencies & Execution Order

- **Phase 1**: T001 ∥ T002 → T003.
- **US1**: T004 needs T001 and T003. **US2**: T005 needs T004. **US3**: T006 needs T005.
- **US4**: T007 and T008 need T005; T009 needs T005.
- **Phase 6**: T010 needs T005 (and is done after T009, so the helper can reuse its patterns);
  T011 ∥ T012; T013 last.

```text
T001 ┐
T002 ┴─► T003 ─► T004 (US1) ─► T005 (US2) ─┬─► T006 (US3)
                                          ├─► T007 ∥ T008 (US4) ─► T009 (US4)
                                          └─────────────────────────────────► T010 ─► T011 ∥ T012 ─► T013
```

## Parallel Opportunities

- T001 (application) ∥ T002 (indexes).
- T007 (OpenAPI test, `test_notifications_endpoints.py`) ∥ T008 (CORS test,
  `test_notifications_cors.py`): different files.
- T011 (README) ∥ T012 (CLAUDE.md).

## Implementation Strategy

1. **MVP**: T001–T005. Listing and fetching work end to end at unit and integration level.
2. **Hardening**: T006 (validation matrix), T007–T009 (OpenAPI, CORS, component with Kafka down).
3. **Constitution VII catch-up and docs**: T010–T013. Then the PR "Implements US-04: Read-only
   notifications API".

Each of the 13 tasks is one commit with a green gate.
