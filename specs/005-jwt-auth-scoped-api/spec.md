# Feature Specification: Authenticate with the Identity token and scope the notifications API

**Feature Branch**: `feature/US-05-jwt-auth-scoped-api`

**Created**: 2026-11-23

**Status**: Draft

**Input**: User description: "US-05: Authenticate with the Identity token and scope the notifications API. Notifications verifies the access tokens issued by ecommerce-identity locally, without calling Identity per request, using its JWKS. CUSTOMER sees only their own notifications; ADMIN sees all. Health and OpenAPI docs are public; default deny. Out of scope: token revocation, rate limiting at the edge, write endpoints, per-field permissions."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A customer reads only their own notifications (Priority: P1)

A signed-in customer calls the notifications API with the access token Identity gave them and
sees only the notifications addressed to them: the list, its total, its pages and its filters all
reflect their own data, and a notification that belongs to someone else looks exactly like one
that does not exist.

**Why this priority**: Today anyone can read every customer's notifications. Closing that leak is
the reason for the story.

**Independent Test**: Store notifications for two customers, then call the list and the detail
endpoints with each customer's token and check that each one sees only their own.

**Acceptance Scenarios**:

1. **Given** notifications for customers A and B, **When** A lists notifications, **Then** only A's
   notifications are returned and `total` counts only A's.
2. **Given** the same data, **When** A lists with `customerId` = B, **Then** the page is empty with
   `total` 0.
3. **Given** the same data, **When** A lists with `customerId` = A, or with `orderId` / `type`
   filters, **Then** the filters apply within A's notifications only.
4. **Given** a notification owned by B, **When** A asks for it by id, **Then** the answer is 404,
   identical to the answer for an unknown id.
5. **Given** a notification owned by A, **When** A asks for it by id, **Then** it is returned.

---

### User Story 2 - Requests without a valid token are refused (Priority: P1)

Every non-public request must carry a valid Identity access token. Any token problem gets the same
generic refusal, so a caller cannot learn why a token was rejected.

**Why this priority**: Without verification the scoping in Story 1 means nothing.

**Independent Test**: Call `GET /notifications` with no token, a malformed token, an expired
token, a token signed by an unknown key, a token with the wrong issuer or audience, and tokens using
`alg: none` or HS256; every call gets the same 401.

**Acceptance Scenarios**:

1. **Given** no `Authorization` header, **When** a protected endpoint is called, **Then** the answer
   is 401 with `WWW-Authenticate: Bearer` and a generic body.
2. **Given** a token that is malformed, expired beyond the clock tolerance, wrongly signed, has the
   wrong issuer or audience, has no `kid`, or uses an algorithm other than RS256 (including `none`
   and HS256 signed with the public key as the secret), **When** a protected endpoint is called,
   **Then** the answer is the same 401 as scenario 1.
3. **Given** a token that expired less than the clock tolerance ago, **When** it is used, **Then** it
   is accepted.
4. **Given** any rejected request, **When** the logs and the response body are inspected, **Then**
   the token does not appear in either.

---

### User Story 3 - An admin reads all notifications (Priority: P2)

An admin (support staff) can list every notification, filter by any customer, and read any
notification by id.

**Why this priority**: Needed for support, but secondary to protecting customers.

**Independent Test**: With an ADMIN token, list without filters (all customers appear), filter by a
customer id (only that customer), and read another user's notification by id.

**Acceptance Scenarios**:

1. **Given** notifications for A and B, **When** an admin lists without filters, **Then** both
   appear and `total` counts both.
2. **Given** the same data, **When** an admin lists with `customerId` = B, **Then** only B's appear.
3. **Given** a notification owned by A, **When** an admin reads it by id, **Then** it is returned.
4. **Given** a user holding both CUSTOMER and ADMIN, **When** they list, **Then** they get the admin
   view (all notifications).
5. **Given** a valid token with neither ADMIN nor a `customerId`, **When** a protected endpoint is
   called, **Then** the answer is 403.

---

### User Story 4 - The service stays available and self-describing (Priority: P3)

Operators and API consumers need the service to start and run its event processing regardless of
Identity, and to see from the API description which routes need which role.

**Why this priority**: Operational robustness and discoverability; not user-facing value on its own.

**Independent Test**: Start the service with Identity unreachable: it starts, health endpoints and
docs answer without a token, events are still consumed and stored; a protected request answers 503
while no keys can be obtained. The OpenAPI document declares a bearer scheme and each route's
access.

**Acceptance Scenarios**:

1. **Given** Identity is unreachable, **When** the service starts, **Then** startup succeeds,
   `/health/live` and `/health/ready` answer without a token, and readiness is not affected.
2. **Given** Identity is unreachable and no keys were ever obtained, **When** a request with a
   token arrives, **Then** the answer is 503 (not 401).
3. **Given** keys were obtained earlier and Identity then becomes unreachable, **When** a token
   signed with a cached key arrives, **Then** it is verified from the cache and accepted.
4. **Given** Identity rotates to a new key, **When** a token with the new `kid` arrives, **Then** the
   keys are refreshed and the token is accepted, and repeated unknown `kid`s trigger at most one
   refresh per minute.
5. **Given** the OpenAPI document, **When** it is read, **Then** it declares the bearer scheme, marks
   health and docs routes public, and states the allowed roles of each protected route.
6. **Given** a browser client on an allowed origin, **When** it sends an `Authorization` header,
   **Then** CORS permits it.
7. **Given** any state of Identity, **When** events arrive on the consumed topics, **Then** they are
   processed, stored and dead-lettered exactly as before, with no token involved.

### Edge Cases

- `Authorization` header with a scheme other than `Bearer`, an empty token, or extra parts → 401.
- Token whose `roles` claim is missing, not a list, or contains only unknown roles → treated as
  having no role: 403 if otherwise valid and no `customerId`; a CUSTOMER role without a
  `customerId` → 403.
- Token with `customerId` but no CUSTOMER role and no ADMIN → 403 (a role is required).
- Token with a `kid` still unknown after a refresh → 401.
- Identity returns a malformed key set → treated as "keys cannot be fetched" (503 if nothing is
  cached, otherwise keep using the cache).
- Many concurrent requests with an unknown `kid` → a single refresh, not one per request.
- A notification id that is not a valid id format → same 404 / 422 behaviour as today, regardless of
  role.
- A route registered without declaring public or roles → the service refuses to start.

## Requirements *(mandatory)*

### Functional Requirements

**Token verification**

- **FR-001**: The service MUST verify Identity access tokens locally using the public keys published
  at `IDENTITY_JWKS_URL` (default `http://localhost:3001/.well-known/jwks.json`), without calling
  Identity per request.
- **FR-002**: Keys MUST be fetched lazily (on first need, not at startup), cached, and refetched only
  when a token carries an unknown `kid`, at most once per minute. Fetching MUST NOT block other
  request or event processing.
- **FR-003**: The service MUST start and run normally when Identity is unreachable.
- **FR-004**: Only RS256 MUST be accepted; `none`, HS256 (including HS256 using the public key as
  the secret) and every other algorithm MUST be rejected. A token MUST carry a `kid`, and only the
  key with that `kid` is used.
- **FR-005**: Issuer and audience MUST match `JWT_ISSUER` and `JWT_AUDIENCE`; expiry MUST be checked
  with a tolerance of `JWT_CLOCK_TOLERANCE_SECONDS` (default 5).
- **FR-006**: Missing, malformed, invalid, expired or wrongly signed tokens MUST all get the same
  401 response with `WWW-Authenticate: Bearer` and the standard error body, with no detail of the
  cause.
- **FR-007**: If keys are needed, cannot be fetched and none are cached, the response MUST be 503.
- **FR-008**: Tokens MUST never appear in logs or response bodies.

**Access control**

- **FR-009**: Every route MUST require authentication unless explicitly declared public. Health
  endpoints and the OpenAPI documentation are public.
- **FR-010**: Every route MUST declare either public access or its allowed roles (`CUSTOMER`,
  `ADMIN`); a route declaring neither MUST stop the service at startup.
- **FR-011**: A valid token that has neither the ADMIN role nor a CUSTOMER role with a `customerId`
  MUST get 403.

**Scoping the read API**

- **FR-012**: `GET /notifications` for a CUSTOMER MUST return only notifications whose `customerId`
  equals the token's `customerId`; the restriction MUST be part of the stored-data query itself, so
  `total`, pagination and the other filters reflect only the caller's data.
- **FR-013**: A CUSTOMER's `customerId` filter that differs from their own MUST return an empty page
  with `total` 0 (not an error).
- **FR-014**: An ADMIN MUST see all notifications and MAY filter by any `customerId`. A user with
  both roles gets the ADMIN view.
- **FR-015**: `GET /notifications/{id}` for a CUSTOMER MUST return 404 for a notification owned by
  someone else, indistinguishable from an unknown id. An ADMIN MAY read any notification.

**Unchanged behaviour and documentation**

- **FR-016**: The OpenAPI document MUST declare the bearer scheme, the public routes and each
  protected route's allowed roles, plus the 401/403/503 responses.
- **FR-017**: CORS MUST allow the `Authorization` header for the configured origins.
- **FR-018**: Kafka consumption, dead-letter handling and the readiness endpoint MUST be unaffected
  and require no token; Identity's availability MUST NOT affect readiness.
- **FR-019**: README, CLAUDE.md (which today states "No authentication") and the constitution MUST
  be updated to describe authentication, the new settings and the access rules.

### Key Entities

- **Authenticated caller**: derived from a verified token — user id (`sub`), optional `customerId`,
  roles (`CUSTOMER`, `ADMIN`). Never stored.
- **Access scope**: either "all notifications" (ADMIN) or "notifications of one customer"
  (CUSTOMER); applied to every read.
- **Key set**: Identity's published public keys, identified by `kid`, cached in memory with the time
  of the last fetch.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In tests, 0 notifications belonging to another customer are ever returned to a
  customer, through the list, its `total`, or the detail endpoint.
- **SC-002**: 100% of the rejected-token cases listed in Story 2 get byte-identical 401 responses.
- **SC-003**: The service starts and processes events with Identity unreachable in 100% of test
  runs.
- **SC-004**: Under a burst of requests with unknown key ids, Identity receives at most one key
  request per minute.
- **SC-005**: No token string appears in any captured log line or error body across the test suite.
- **SC-006**: Every route in the API description states its access (public or roles).

## Assumptions

- Token claims follow the ecommerce-identity README: `sub`, `customerId` (customers only), `roles`,
  `iss`, `aud`, `iat`, `exp`, `jti`, header `kid`. Local defaults match ecommerce-infra:
  `JWT_ISSUER=http://localhost:3001`, `JWT_AUDIENCE=ecommerce`.
- Roles are read from the token as issued; role changes take effect when the token expires
  (no revocation, out of scope).
- The 503 body is the same generic `{"detail": ...}` shape as today's 503.
- Tests sign their own tokens with a test key served by a local fake key endpoint; no running
  Identity service is needed for the gate.
- Removing "No authentication" from the HTTP rules requires a constitution amendment (MINOR bump),
  done in this change set; no ADR beyond one recording the local-verification choice is needed.
- Out of scope: token revocation, edge rate limiting, write endpoints, per-field permissions.
