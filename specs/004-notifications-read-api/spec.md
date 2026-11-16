# Feature Specification: Read-only notifications API (US-04)

**Feature Branch**: `feature/US-04-notifications-read-api`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "US-04: Read-only API to list and fetch notifications, so that clients
(the future web frontend) can show the notifications recorded by US-01 to US-03. List with
combinable filters and stable pagination, fetch one by id, 422 for bad input, 404 for unknown ids,
complete OpenAPI, CORS from configuration, works without Kafka, no authentication (local/demo)."

## Clarifications

### Session 2026-11-16

- Q: Which id format do notifications use in the API? → A: The one already used since US-01
  (UUID); kept as it is.
- Q: How is `total` computed? → A: An exact count of the documents matching the filters; fine at
  this scale.
- Q: What is the shape of error bodies? → A: FastAPI's standard error format (`{"detail": ...}`;
  for 422 the standard list of validation errors).
- Q: Are authentication, other sort orders or search part of this story? → A: No, out of scope.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A client lists notifications, newest first (Priority: P1)

A client (for example the future web frontend) asks for the recorded notifications and receives
them newest first, one page at a time, together with the total number of matching notifications,
so it can show a list with "next page" controls.

**Why this priority**: Listing is what makes the recorded notifications visible at all; every
other capability of this story refines it.

**Independent Test**: With notifications recorded for several customers, orders and types, ask for
the list with and without filters and pages; check order, filtering, `total`, `limit` and `offset`.

**Acceptance Scenarios**:

1. **Given** 25 recorded notifications, **When** the client lists without parameters, **Then** it
   receives the 20 newest (creation time descending), `total` 25, `limit` 20, `offset` 0.
2. **Given** the same data, **When** the client asks for `limit` 20 and `offset` 20, **Then** it
   receives the remaining 5, and no notification appears on both pages or on neither.
3. **Given** notifications with the same creation time, **When** they are paged, **Then** their
   order is stable across requests (ties broken by id) and paging never skips or repeats one.
4. **Given** notifications of several customers, orders and types, **When** the client filters by
   any combination of `customerId`, `orderId` and `type`, **Then** only notifications matching all
   given filters are returned and `total` counts exactly those.
5. **Given** a filter that matches nothing, **When** the client lists, **Then** it receives 200 with
   an empty `items` list and `total` 0.

---

### User Story 2 - A client fetches one notification (Priority: P1)

A client opens one notification by its id and receives all its fields; an unknown id gives a clear
"not found" answer rather than an error.

**Why this priority**: Needed for a detail view and for links to a single notification; small and
independent of listing.

**Independent Test**: Fetch an existing notification by id (200, all fields) and a well-formed id
that does not exist (404 with a clear error body).

**Acceptance Scenarios**:

1. **Given** a recorded notification `N`, **When** the client fetches it by id, **Then** it receives
   200 with `id`, `customerId`, `orderId`, `type`, `channel`, `message`, `status`, `createdAt`
   (ISO 8601, UTC) and `sourceEventId`, equal to what was recorded.
2. **Given** a well-formed id that matches no notification, **When** the client fetches it,
   **Then** it receives 404 with a body that says which notification was not found.

---

### User Story 3 - Bad requests are rejected clearly, never as server errors (Priority: P2)

A client that sends an invalid request learns what was wrong; the service never answers bad input
with a server error, and never offers a way to change data.

**Why this priority**: Protects the P1 stories and the clients built on them; mostly validation.

**Independent Test**: Send an unknown `type`, an out-of-range `limit` / `offset`, and malformed ids
in the path and in the filters; every answer is 422 with details. Try write methods; none succeeds.

**Acceptance Scenarios**:

1. **Given** `type` set to a value that is not one of the four notification types, **When** the
   client lists, **Then** it receives 422 with details naming `type` and the allowed values.
2. **Given** `limit` 0 or 101, or `offset` -1, or a non-numeric value, **When** the client lists,
   **Then** it receives 422 with details naming the parameter.
3. **Given** a malformed id in the path or a malformed `customerId` / `orderId` filter, **When** the
   client calls the API, **Then** it receives 422 with details naming the field.
4. **Given** any request to create, change or delete a notification (`POST`, `PUT`, `PATCH`,
   `DELETE` on the notification paths), **When** it is sent, **Then** it is refused (405) and no
   data changes.

---

### User Story 4 - The API is documented and usable while Kafka is down (Priority: P3)

Developers can discover the API from its generated documentation, a browser frontend on a
configured origin can call it, and reading works even when the event broker is unreachable.

**Why this priority**: Operational quality; the data and endpoints of US1–US3 come first.

**Independent Test**: Open the generated API document and check that every endpoint, parameter and
response field is described; call the list endpoint with the broker unreachable; call it from a
configured and from an unconfigured origin.

**Acceptance Scenarios**:

1. **Given** the generated API document, **When** it is inspected, **Then** both endpoints, every
   query and path parameter and every response field have a description, and the 404 and 422
   responses are documented.
2. **Given** the event broker is unreachable, **When** a client lists or fetches notifications,
   **Then** the answers are the same as with the broker available.
3. **Given** `CORS_ALLOWED_ORIGINS` contains a frontend origin, **When** that origin calls the API,
   **Then** the browser is allowed to read the answer; **given** it is empty (the default),
   **Then** no cross-origin access is granted.

---

### Edge Cases

- **`offset` beyond the last page**: 200 with empty `items` and the real `total`.
- **Unknown query parameters**: ignored (no error), as is usual for read APIs.
- **Filters with different letter case**: ids are compared as canonical lowercase UUIDs; an
  uppercase UUID in a filter or path is accepted and matches.
- **A notification recorded between two page requests**: it appears at the top; pages of an
  unchanged data set are stable, but a growing data set can shift items between pages (no cursors
  in this scope).
- **Database unreachable**: the API answers 503 (the readiness check already reports it); it never
  pretends the result is empty.
- **Very large `total`**: counting is exact; no approximation.

## Requirements *(mandatory)*

### Functional Requirements

**Listing**

- **FR-001**: `GET /notifications` MUST return notifications ordered by creation time descending,
  with the notification id (descending) as tie-break, so paging an unchanged data set is stable.
- **FR-002**: It MUST accept optional, combinable filters `customerId` (UUID), `orderId` (UUID) and
  `type` (one of `ORDER_RECEIVED`, `ORDER_CONFIRMED`, `ORDER_CANCELLED`, `PAYMENT_REFUNDED`); a
  notification is returned only if it matches every given filter.
- **FR-003**: It MUST accept `limit` (integer, default 20, 1–100) and `offset` (integer, default 0,
  ≥ 0).
- **FR-004**: The response MUST be an object with `items` (the page), `total` (number of
  notifications matching the filters, independent of paging), `limit` and `offset` (as applied).
- **FR-005**: A query matching nothing MUST return 200 with `items` empty and `total` 0.

**Fetching**

- **FR-006**: `GET /notifications/{id}` MUST return the notification with that id, or 404 with
  FastAPI's standard error body (`{"detail": "..."}`) naming the id when none exists.

**Representation**

- **FR-007**: Each notification MUST be represented with `id`, `customerId`, `orderId`, `type`,
  `channel`, `message`, `status`, `createdAt` (ISO 8601, UTC, with offset) and `sourceEventId`, with
  the values recorded by US-01 to US-03 unchanged.
- **FR-008**: The response representation MUST be defined separately from the stored form and from
  the business objects (constitution Principles I and V), so that either can change without
  changing the other.

**Validation and safety**

- **FR-009**: Invalid input (unknown `type`, `limit` or `offset` out of range or not an integer,
  malformed UUID in the path or in a filter) MUST return 422 with details naming the invalid input;
  bad input MUST NOT cause a 5xx response.
- **FR-010**: No endpoint may create, change or delete data; write methods on these paths MUST be
  refused (constitution Principle II).
- **FR-011**: If the database is unreachable, the endpoints MUST answer 503 with an error body, not
  an empty result.

**Documentation, configuration, operations**

- **FR-012**: Every endpoint, parameter and response field MUST carry a description in the
  generated API document, including the 404 and 422 responses.
- **FR-013**: Cross-origin access MUST be controlled only by `CORS_ALLOWED_ORIGINS` (comma
  separated, empty by default = none), read-only methods only; no origin is hard-coded.
- **FR-014**: The endpoints MUST work when the event broker is unreachable (they depend only on the
  database).
- **FR-015**: Filtering and sorting MUST be served by indexes declared in the versioned index
  module: the existing customer index, plus indexes for the order filter and the type filter, each
  supporting the newest-first sort. The README MUST list the indexes.
- **FR-016**: The README MUST document both endpoints (parameters, responses, examples) and state
  that the API has no authentication and is for local and demo use only.
- **FR-017**: Constitution Principle VII (v1.1.0): this story introduces the read API, so in the
  same change set every existing component test that records a notification (US-01 to US-03) MUST
  also read it back through `GET /notifications/{id}` and compare it with what was stored.

### Key Entities

- **Notification (read view)**: the fields of FR-007 as returned to clients; read-only.
- **Notification page**: `items`, `total`, `limit`, `offset`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A client can show any customer's or order's notifications newest first; for an
  unchanged data set, walking all pages returns every matching notification exactly once (0
  duplicates, 0 omissions).
- **SC-002**: With 10 000 stored notifications, a filtered page is returned within 300 ms on a
  developer machine.
- **SC-003**: 100% of invalid requests in the test suite receive 422 (or 404 for unknown ids), and
  0 receive a server error.
- **SC-004**: 100% of endpoints, parameters and response fields are described in the generated API
  document.
- **SC-005**: Reading works with the event broker down: the same answers as with it up.
- **SC-006**: Every notification recorded by the component tests of US-01 to US-03 is also read
  back through the API with identical content.

## Assumptions

- **Read model**: the API reads the existing `notifications` collection directly; no separate read
  store.
- **Error body** (clarified): FastAPI's standard format. 404 and 503 are `{"detail": "<message>"}`;
  422 is the standard `{"detail": [ {loc, msg, type, ...}, ... ]}` list.
- **Ids** (clarified): the format used since US-01, unchanged: notification, customer and order ids
  are UUIDs; anything else is a malformed id (422).
- **Counting** (clarified): `total` is an exact count of the documents matching the filters;
  acceptable at the expected volumes.
- **Pagination**: offset-based, as requested; cursor pagination is out of scope.
- **CORS**: the existing behaviour from the bootstrap (origins from `CORS_ALLOWED_ORIGINS`, read-only
  methods) is kept and covered by tests; no code change expected.
- **Security**: no authentication or rate limiting; local and demo use only (README).
- **Out of scope**: any write endpoint, authentication, text search, other sort orders, streaming or
  websockets, per-customer counts.
