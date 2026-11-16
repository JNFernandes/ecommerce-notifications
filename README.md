# ecommerce-notifications

The Python microservice that records customer notifications for order events in the ecommerce
system (Orders: NestJS, Payments: .NET, Inventory: Java, Notifications: Python).

Sending is simulated. Each notification is stored in MongoDB with channel `email`, and a log line
is written; there is no real email provider. All business input arrives as Kafka events. The HTTP
API is read-only, and the service publishes no events.

The rules for this codebase are in the [constitution](.specify/memory/constitution.md).
Architectural decisions are in [docs/adr](docs/adr/README.md).

> **Status:** US-01 to US-04 done. The service consumes `orders.order-placed`,
> `orders.order-confirmed`, `orders.order-cancelled` and `payments.payment-refunded`, and serves
> the recorded notifications through a read-only HTTP API.

## Consumed topics

| Topic | Notification | Notes | Status |
|---|---|---|---|
| `orders.order-placed` | `ORDER_RECEIVED` | Also stores the order's customer and total (`OrderContext`), see [ADR 0003](docs/adr/0003-consume-order-placed-for-refund-customer.md). Message: `Your order <orderId> for <totalAmount> was received.` | implemented (US-01) |
| `orders.order-confirmed` | `ORDER_CONFIRMED` | Customer from the event; no `OrderContext`. Message: `Your order <orderId> was confirmed.` | implemented (US-02) |
| `orders.order-cancelled` | `ORDER_CANCELLED` | Customer from the event; no `OrderContext`. Message: `Your order <orderId> was cancelled: <reason>.`, or `Your order <orderId> was cancelled.` without a reason. The reason is trimmed, kept on one line, loses one trailing period, and is cut at 500 characters (`…`); a `reason` that is not a string or `null` makes the message invalid. | implemented (US-02) |
| `payments.payment-refunded` | `PAYMENT_REFUNDED` | The event has no `customerId`; the customer comes from the `OrderContext` of the event's `orderId` (never `aggregateId`, which is the payment id). Message: `Your payment of <amount> <currency> for order <orderId> was refunded.` (two decimals). A missing context is retried, then dead-lettered (see Known limitation). | implemented (US-03) |

Payloads are defined in
[`ecommerce-infra/docs/events.md`](../ecommerce-infra/docs/events.md), the source of truth for
every event contract.

### Processing guarantees

- **One consumer, one routing table.** A single consumer subscribes to every topic above and
  routes each message by topic to its parser and exactly one application service method
  (`build_routing_table` in `src/notifications/main.py`). Adding a topic is one registration.
  The three order topics are independent: a confirmation or cancellation is recorded even if
  the order's placement was never received.
- **One notification per event.** A notification is stored under a unique index on its source
  event id, so a redelivered event changes nothing and is not an error
  ([ADR 0002](docs/adr/0002-idempotency-without-transactions.md)). The simulated send (a log line
  on `notifications.send`) is written only after the notification is stored, never for a
  duplicate.
- **Offset after save.** The Kafka offset of a message is committed only after it was stored or
  dead-lettered. Messages are processed one at a time per instance.
- **Invalid messages** (not UTF-8, not JSON, missing or invalid fields, `version` other than 1)
  go straight to the `inbound_dead_letters` collection, with the original payload, topic,
  partition, offset and the reason. They are not retried.
- **Failing messages** are retried with capped exponential backoff (see `CONSUMER_RETRY_*`
  below), then dead-lettered with the last error and the number of attempts.
- **No message is skipped.** If the dead-letter collection itself cannot be written (MongoDB
  down), the consumer holds the message and processes it again after `CONSUMER_RETRY_MAX_DELAY_SECONDS`
  until it is stored or dead-lettered.
- Amounts are read and stored exactly (`Decimal`, `Decimal128` in MongoDB), never as floats.

### Known limitation: refund before its order context

A refund notification needs the customer, which only the order's placement provides (stored as
the `OrderContext`). If no context exists for the refund's `orderId`, the refund is retried with
the `CONSUMER_RETRY_*` backoff (about 15 s with the defaults) and then stored in
`inbound_dead_letters` with `errorType: "OrderContextNotFoundError"` and the order id in
`errorMessage`. No notification is ever stored without a customer.

In normal operation the placement is processed long before the refund (a refund follows a
cancellation, which follows the placement). It can happen while the service catches up on a
backlog: messages are processed one at a time per instance, so the placement waiting behind a
retrying refund cannot be processed in time. **Such refunds are not recorded later**, even after
the context arrives; replaying dead letters is not provided. Find them with:

```js
db.inbound_dead_letters.find({ errorType: "OrderContextNotFoundError" })
```

## HTTP API

| Method and path | Purpose | Status |
|---|---|---|
| `GET /health/live` | Liveness: `UP` whenever the process answers. | available |
| `GET /health/ready` | Readiness: 200 when MongoDB answers, 503 otherwise. Reports the Kafka consumer state (`CONNECTING`, `CONNECTED`, `DISCONNECTED`) without failing when Kafka is down. | available |
| `GET /notifications` | List notifications, newest first. Filters `customerId`, `orderId`, `type`; `limit` / `offset` pagination. | available |
| `GET /notifications/{id}` | One notification. | available |

The OpenAPI document is served at `/openapi.json`, with interactive docs at `/docs`.

> **No authentication.** The API has no authentication or rate limiting and is meant for
> **local and demo use only**. It is read-only: no endpoint creates, changes or deletes data
> (other methods answer 405). It only needs MongoDB, so it keeps answering while Kafka is down.

### `GET /notifications`

| Query parameter | Type | Default | Rule |
|---|---|---|---|
| `customerId` | UUID | — | optional filter |
| `orderId` | UUID | — | optional filter |
| `type` | `ORDER_RECEIVED`, `ORDER_CONFIRMED`, `ORDER_CANCELLED`, `PAYMENT_REFUNDED` | — | optional filter |
| `limit` | integer | 20 | 1–100 |
| `offset` | integer | 0 | ≥ 0 |

Filters combine with AND. Order: `createdAt` descending, then `id` descending, so paging an
unchanged data set never skips or repeats a notification. `total` is the exact number of matching
notifications.

```sh
curl "http://localhost:8000/notifications?customerId=3fa85f64-5717-4562-b3fc-2c963f66afa6&type=PAYMENT_REFUNDED&limit=10"
```

```json
{
  "items": [
    {
      "id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
      "customerId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "orderId": "270bd352-0309-4356-9fa3-925a5450c361",
      "type": "PAYMENT_REFUNDED",
      "channel": "email",
      "message": "Your payment of 42.50 USD for order 270bd352-0309-4356-9fa3-925a5450c361 was refunded.",
      "status": "SENT",
      "createdAt": "2026-11-16T21:30:02.208000Z",
      "sourceEventId": "6a1b2c3d-0000-4000-8000-000000000401"
    }
  ],
  "total": 1,
  "limit": 10,
  "offset": 0
}
```

### `GET /notifications/{id}`

Returns one notification (same shape as an `items` element).

### Errors (FastAPI's standard format)

| Status | When | Body |
|---|---|---|
| 404 | no notification has this id | `{"detail": "notification <id> not found"}` |
| 422 | unknown `type`; `limit` / `offset` out of range or not an integer; malformed UUID in a filter or the path | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` |
| 503 | MongoDB unreachable (never reported as an empty result) | `{"detail": "database unavailable"}` |
| 405 | any method other than `GET` | standard |

### Indexes (schema version 3)

Declared only in `src/notifications/infrastructure/indexes.py` and applied at startup; obsolete
indexes of earlier versions are dropped (`OBSOLETE_INDEX_NAMES`).

| Collection | Index | Serves |
|---|---|---|
| `notifications` | `uq_notifications_source_event_id` (`sourceEventId`, unique) | one notification per event |
| `notifications` | `ix_notifications_created_id` (`createdAt -1, _id -1`) | the unfiltered list |
| `notifications` | `ix_notifications_customer_created_id` (`customerId, createdAt -1, _id -1`) | the `customerId` filter |
| `notifications` | `ix_notifications_order_created_id` (`orderId, createdAt -1, _id -1`) | the `orderId` filter |
| `notifications` | `ix_notifications_type_created_id` (`type, createdAt -1, _id -1`) | the `type` filter |
| `inbound_dead_letters` | `uq_dead_letters_topic_partition_offset` (unique) | one dead letter per message |
| `order_context` | `_id` = order id | unique by definition |

Each list query is served by one of these indexes in the requested order, so MongoDB never sorts
in memory (checked by the integration tests with `explain()`).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) 0.11 or later. uv installs Python 3.12 for the project (pinned
  in `.python-version`); do not rely on the machine's global Python.
- Docker, for the local infrastructure and for the Testcontainers-based tests.
- The sibling repository [`ecommerce-infra`](../ecommerce-infra) (MongoDB and Kafka).

## Running locally

1. Start the shared infrastructure from the `ecommerce-infra` repository:

   ```sh
   cd ../ecommerce-infra
   docker compose up -d
   ```

   This gives MongoDB on `localhost:27017` (`mongo_user` / `mongo_pass`, `authSource=admin`) and
   Kafka on `localhost:9092`, with the topics already created.

2. Install the dependencies and start the service:

   ```sh
   uv sync
   uv run uvicorn notifications.main:create_app --factory --port 8000 --reload
   ```

3. Check it: `curl http://localhost:8000/health/ready`.

The service starts even when Kafka is not reachable. The consumer manager keeps retrying in the
background with exponential backoff, and readiness shows the Kafka state. MongoDB must be reachable
at startup because the indexes are applied then.

### Troubleshooting: "Authentication failed" on startup

If startup fails with `pymongo.errors.OperationFailure: Authentication failed`, another MongoDB
is probably installed on the machine (for example a Windows service) and listens on
`127.0.0.1:27017`. That address is more specific than Docker's `0.0.0.0:27017`, so `localhost`
reaches the other MongoDB, which does not have the `mongo_user` account.

Check what listens on the port:

```powershell
netstat -ano | findstr ":27017"
```

If you see both `127.0.0.1:27017` and `0.0.0.0:27017`, point the service at `127.0.0.2`. It is
also this computer, but only Docker's MongoDB listens there:

```powershell
# current terminal only
$env:MONGODB_URI = 'mongodb://mongo_user:mongo_pass@127.0.0.2:27017/?authSource=admin'

# or permanently for your user (open a new terminal afterwards)
[Environment]::SetEnvironmentVariable('MONGODB_URI', 'mongodb://mongo_user:mongo_pass@127.0.0.2:27017/?authSource=admin', 'User')
```

The other MongoDB keeps working on `127.0.0.1`. The tests and the Docker image are not affected:
the tests start their own MongoDB container, and the image connects to `mongo:27017` inside the
compose network.

### Configuration

All settings come from environment variables. The defaults match `ecommerce-infra` on localhost.

| Variable | Default | Meaning |
|---|---|---|
| `MONGODB_URI` | `mongodb://mongo_user:mongo_pass@localhost:27017/?authSource=admin` | MongoDB connection string |
| `MONGODB_DATABASE` | `notifications` | Database name |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Comma-separated Kafka brokers |
| `KAFKA_GROUP_ID` | `notifications-service` | Consumer group id |
| `KAFKA_TOPIC_ORDER_PLACED` | `orders.order-placed` | Topic of `OrderPlaced` events (`ORDER_RECEIVED`) |
| `KAFKA_TOPIC_ORDER_CONFIRMED` | `orders.order-confirmed` | Topic of `OrderConfirmed` events (`ORDER_CONFIRMED`) |
| `KAFKA_TOPIC_ORDER_CANCELLED` | `orders.order-cancelled` | Topic of `OrderCancelled` events (`ORDER_CANCELLED`) |
| `KAFKA_TOPIC_PAYMENT_REFUNDED` | `payments.payment-refunded` | Topic of `PaymentRefunded` events (`PAYMENT_REFUNDED`) |
| `CORS_ALLOWED_ORIGINS` | empty | Comma-separated origins allowed to call the API from a browser (`GET` only), e.g. `http://localhost:5173` for a frontend dev server. Empty = no cross-origin access. Add the frontend origin here, never in code. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `CONSUMER_RETRY_MAX_ATTEMPTS` | `5` | Attempts per consumed message, the first included (`>= 1`; `1` means no retries) |
| `CONSUMER_RETRY_BASE_DELAY_SECONDS` | `1.0` | Pause before the second attempt (`> 0`) |
| `CONSUMER_RETRY_MULTIPLIER` | `2.0` | Growth factor of the pause between attempts (`>= 1`) |
| `CONSUMER_RETRY_MAX_DELAY_SECONDS` | `10.0` | Cap on any single pause (`>=` the base delay); also the pause between rounds while the dead-letter collection cannot be written |

The pause before attempt `n` (n ≥ 2) is `min(BASE_DELAY × MULTIPLIER^(n-2), MAX_DELAY)`. With the
defaults the pauses are 1, 2, 4 and 8 seconds, about 15 seconds per failing message. An invalid
value stops the service at startup with an error naming the variable.

The topic defaults are the contract names in `ecommerce-infra/docs/events.md`; override them only
for tests or a renamed environment. The four names must differ.

### Docker

```sh
docker build -t ecommerce-notifications .
docker run --rm -p 8000:8000 --network ecommerce_default \
  -e MONGODB_URI='mongodb://mongo_user:mongo_pass@mongo:27017/?authSource=admin' \
  -e KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
  ecommerce-notifications
```

The image is a multi-stage build: uv installs the locked dependencies in a build stage, and the
runtime stage is `python:3.12-slim` with only the virtual environment, running as a non-root user.
Its health check calls `/health/live`. Inside the compose network, Kafka listens on `kafka:29092`.

## Quality gate

Run after every change, in this order. All four must pass before a commit.

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest --cov
```

- `ruff format` (without `--check`) fixes formatting; `ruff check --fix` fixes the fixable lint
  findings.
- `mypy` runs in strict mode over `src` and `tests`.
- `pytest --cov` runs unit, integration and component tests and fails below 80% line and branch
  coverage of the `domain` and `application` packages. Docker must be running: the tests start
  MongoDB (`mongo:7`) and Kafka (`confluentinc/cp-kafka:7.6.1`) containers. They do not use the
  ecommerce-infra stack, so it may be running or not. The Kafka outage tests pause the test broker
  with `docker pause`.

## Project layout

```text
src/notifications/
  main.py            Composition root: settings, MongoDB client, consumer manager, lifespan
  api/               FastAPI routers and response models (read-only HTTP surface)
  messaging/         Kafka handlers, retry/dead-letter processor, background consumer manager
  application/       Application services and the ports (Protocols) they use
  domain/            Business logic in plain Python (no FastAPI, aiokafka, pymongo or Pydantic)
  infrastructure/    Settings, MongoDB client, repositories, simulated sender, index module
tests/
  unit/              Domain, application and messaging in isolation, with fakes; no containers
  integration/       Repositories and indexes against a real MongoDB container
  component/         The full application against real MongoDB and Kafka containers
  support/           Fakes, event builders, Kafka helpers and polling (no fixed sleeps in tests)
docs/adr/            Architecture Decision Records
```

Layering: Messaging and API → Application service → Domain → Repository. Consumers and routers
only do I/O and call one application service method.
