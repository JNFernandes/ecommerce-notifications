# Research: Order confirmed and order cancelled notifications (US-02)

Each entry: **Decision**, **Rationale**, **Alternatives considered**. Everything from US-01 research
(R1 lossless parsing, R3 idempotent writes, R4 consume loop, R5 retryable failures, R6 backoff,
R9 startup) is reused unchanged and not repeated here.

## R1. Topic routing table

**Decision**:
- Replace the manager's list of `MessageHandler`s with an explicit **routing table**
  (`messaging/routing.py`).
- `Route[C]` is a frozen generic dataclass with three fields:
  - `topic: str`;
  - `parse: Callable[[ConsumedMessage], C]`, which raises `InvalidMessageError`;
  - `handle: Callable[[C], Awaitable[object]]`, which is exactly one application service method.
- `RoutingTable` is built from routes and rejects a duplicate topic at construction. It exposes
  `topics` (for the subscription) and `async handle(message)`, which looks up the route,
  parses, then calls the method once. It is the single `MessageHandler` given to the processor.
- `MessageHandler` drops its `topic` attribute and keeps only `async handle(message)`: the
  manager now takes the subscription from `routing.topics`, and the processor never read `topic`.
- Routes are stored behind a small `Any`-free protocol (`topic` + `async run(message)`), so the
  table holds routes of different command types without `Any` (Principle V).
- A message on a topic with no route raises `InvalidMessageError("no route for topic <t>")`, so it
  is dead-lettered, never dropped silently (FR-001).
- The manager takes `routing: RoutingTable` instead of `handlers`, and passes the table to
  `processor.process(message, routing)`; the processor is unchanged.
- Adding a topic = one `Route(...)` line in `main.py` plus its parser and service method. The
  consumer, processor and commit logic do not change.

**Rationale**: This is what the user asked for (topic → parser + service method). It keeps
consumers free of logic (Principle I): the route only connects a parser to one service method.
The generic `Route[C]` keeps mypy strict end to end (parser output type = method input type).

**Alternatives considered**:
- Keep one handler class per topic (`OrderPlacedHandler` style): the classes would all be the
  same "parse then call" boilerplate.
- One consumer per topic: rejected in the clarification ("one consumer subscribed to all
  topics").

## R2. Shared envelope validation for order events

**Decision**:
- Move the JSON-type guards (`_json_string`, `_json_integer`, `_json_number`) and the common
  order envelope into `messaging/events.py`.
- `OrderEventEnvelope` is a frozen Pydantic model with `eventId`, `aggregateId`, `occurredAt`,
  `version` and `customerId`.
- Three models extend it:
  - `OrderPlacedEvent` adds `totalAmount`;
  - `OrderConfirmedEvent` adds nothing;
  - `OrderCancelledEvent` adds `reason: str | None`, guarded so that only a JSON string or `null`
    is accepted (clarification Q2).
- One generic `parse_event(message, model)` decodes with `decode_json_object` and maps
  `ValidationError` to `InvalidMessageError` with the `field: problem` list.

**Rationale**: The three order events share their envelope and their rules. One copy of the
guards keeps the US-01 strictness (`true` / `1.0` are not `version: 1`) for every topic.
`use_attribute_docstrings=True` keeps a docstring on every field, including inherited ones.

**Alternatives considered**: three independent models (duplicated guards and docstrings).

## R3. Reason normalisation lives in the domain

**Decision**: `domain/messages.py` gets `normalise_reason(raw: str | None) -> str | None`:

1. replace each run of `\r`, `\n` and `\t` with one space;
2. trim;
3. remove one trailing `.`, then trim again;
4. return `None` if the result is empty;
5. if the result is longer than 500 characters, keep the first 500 and append `…`.

Templates are pure functions:
- `order_confirmed_message(order_id)` returns `Your order <id> was confirmed.`
- `order_cancelled_message(order_id, reason)` returns `Your order <id> was cancelled: <reason>.`,
  or `Your order <id> was cancelled.` when there is no reason.

**Rationale**: What the customer reads is a business rule (Principle I: domain). The parser only
checks the JSON type; the meaning of "no reason" belongs to the domain. Normalising at build time
makes the stored text deterministic (FR-007).

**Alternatives considered**: normalising in the Pydantic model (puts business rules in messaging).

## R4. Application services and the shared "store, then send" step

**Decision**:
- `application/recording.py` holds `RecordOutcome` (moved here, re-exported by
  `record_order_received`) and `async record_and_send(notification, notifications, sender) ->
  RecordOutcome`. It is the existing US-01 sequence: `add`, then `send` only when stored, or log
  "already recorded".
- `RecordOrderReceivedService` uses it, with no behaviour change.
- New `RecordOrderStatusService` with two methods:
  - `record_confirmed(command: OrderConfirmedCommand) -> RecordOutcome`;
  - `record_cancelled(command: OrderCancelledCommand) -> RecordOutcome`.

  Each builds the notification through a domain factory and calls `record_and_send`. There are no
  business rules here, and no use of `OrderContext` (FR-009).
- The duplicate log line becomes generic: `Event already recorded: type=<T> sourceEvent=<id>
  order=<id>`. The US-01 test asserting `already recorded: sourceEvent=` still matches the
  substring `sourceEvent=`, and it is updated to the new text.

**Rationale**: Two topics with the same sequence; one helper avoids three copies of the
idempotent store-then-send logic, the part most worth keeping identical.

**Alternatives considered**: one service class per event (two near-identical classes); a generic
"record any notification" method called directly by the routes (the routes would then build
domain objects, which would be logic in messaging).

## R5. Domain factories

**Decision**:
- Add `NotificationType.ORDER_CONFIRMED` and `ORDER_CANCELLED`. They did not exist yet; the user
  input assumed they did.
- Add `Notification.order_confirmed(...)` and `Notification.order_cancelled(..., reason)`.
- All three factories share a private `_create(...)` that applies `EMAIL`, `SENT` and
  `require_utc_millis(now)`.

## R6. Topic names as settings

**Decision**: Three settings, each with the contract name as its default:
- `KAFKA_TOPIC_ORDER_PLACED`, default `orders.order-placed`;
- `KAFKA_TOPIC_ORDER_CONFIRMED`, default `orders.order-confirmed`;
- `KAFKA_TOPIC_ORDER_CANCELLED`, default `orders.order-cancelled`.

Validation: each is non-empty, and they are pairwise distinct (the routing table also rejects
duplicates). `main.py` builds the routing table from them. The `ORDER_PLACED_TOPIC` constant is
removed.

**Rationale**: Requested in the plan input ("add the two topic names to the settings"). Including
`orders.order-placed` keeps all routes built the same way. Defaults equal the contract, so nothing
needs to be set in any environment.

**Alternatives considered**: keeping constants (US-01 R8), overridden by the user's instruction.

## R7. Tests

**Decision**:
- **Domain unit tests**: both templates; reason normalisation (absent, `null`, empty,
  whitespace, line breaks, trailing period, 500 vs 501 characters, deterministic); both factories.
- **Application unit tests**: `record_confirmed` and `record_cancelled` (stored and sent once,
  duplicate not sent, repository error propagates); `record_and_send`. The service takes no order
  context repository at all, so FR-009 holds by construction.
- **Messaging unit tests**:
  - parsers for both events: valid; each required field missing; bad UUID; `version`; bad JSON;
    `reason` absent / `null` / string / number / boolean / object;
  - routing table: `handle` reaches the right route, no route → `InvalidMessageError`, duplicate topic
    rejected, `topics`.
- **Settings tests**: defaults, environment override, duplicate topics rejected.
- **Integration** (real MongoDB): a redelivered confirmation and a redelivered cancellation each
  stay one document under the unique `sourceEventId` index; the mapping round trip for the new
  types.
- **Component** (real Kafka + MongoDB):
  - one confirmation and two cancellations (with and without a reason) → stored notifications with
    the exact messages;
  - redelivery of a confirmation → one notification, offset committed;
  - an invalid cancellation (`reason: 42`) followed by a valid one → one dead letter (attempts 1),
    one notification;
  - one app consumes all three topics (readiness `CONNECTED`, one consumer).
- **Topics in tests**: `create_topic` for the two new topics in the component fixtures.

## R8. Contract file

**Decision**: In a linked ecommerce-infra PR, update `docs/events.md`: in the Topics table, the
Notifications consumer of `orders.order-confirmed` and `orders.order-cancelled` becomes
**implemented**. In the cancelled section, add one sentence: Notifications shows `reason` in the
message when present. No payload change.
