# ecommerce-notifications

The Python microservice that records customer notifications for order events in the ecommerce
system (Orders: NestJS, Payments: .NET, Inventory: Java, Notifications: Python).

Sending is simulated. Each notification is stored in MongoDB with channel `email`, and a log line
is written; there is no real email provider. All business input arrives as Kafka events. The HTTP
API is read-only, and the service publishes no events.

The rules for this codebase are in the [constitution](.specify/memory/constitution.md).
Architectural decisions are in [docs/adr](docs/adr/README.md).

> **Status:** project bootstrap. The service starts, connects to MongoDB and Kafka, and answers
> the health endpoints. The consumers and the notification queries below come with their user
> stories.

## Consumed topics

| Topic | Notification | Notes |
|---|---|---|
| `orders.order-placed` | `ORDER_RECEIVED` | Also stores the order's customer and total (`OrderContext`), see [ADR 0003](docs/adr/0003-consume-order-placed-for-refund-customer.md). |
| `orders.order-confirmed` | `ORDER_CONFIRMED` | |
| `orders.order-cancelled` | `ORDER_CANCELLED` | The message includes the cancellation reason when present. |
| `payments.payment-refunded` | `PAYMENT_REFUNDED` | The event has no `customerId`; the customer comes from the `OrderContext`. |

Payloads are defined in
[`ecommerce-infra/docs/events.md`](../ecommerce-infra/docs/events.md), the source of truth for
every event contract.

Each event is processed at most once per `eventId`: a notification is stored under a unique index
on its source event id, so a redelivered event changes nothing
([ADR 0002](docs/adr/0002-idempotency-without-transactions.md)). Messages that still fail after
retries, and invalid messages, are stored in the `inbound_dead_letters` collection.

## HTTP API

| Method and path | Purpose | Status |
|---|---|---|
| `GET /health/live` | Liveness: `UP` whenever the process answers. | available |
| `GET /health/ready` | Readiness: 200 when MongoDB answers, 503 otherwise. Reports the Kafka consumer state (`CONNECTING`, `CONNECTED`, `DISCONNECTED`) without failing when Kafka is down. | available |
| `GET /notifications` | List notifications, newest first. Filters `customerId`, `orderId`, `type`; `limit` / `offset` pagination. | planned |
| `GET /notifications/{id}` | One notification. | planned |

The OpenAPI document is served at `/openapi.json`, with interactive docs at `/docs`.

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

### Configuration

All settings come from environment variables. The defaults match `ecommerce-infra` on localhost.

| Variable | Default | Meaning |
|---|---|---|
| `MONGODB_URI` | `mongodb://mongo_user:mongo_pass@localhost:27017/?authSource=admin` | MongoDB connection string |
| `MONGODB_DATABASE` | `notifications` | Database name |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Comma-separated Kafka brokers |
| `KAFKA_GROUP_ID` | `notifications-service` | Consumer group id |
| `CORS_ALLOWED_ORIGINS` | empty | Comma-separated origins allowed to call the API (`GET` only) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

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
- `pytest --cov` runs unit and component tests and fails below 80% line and branch coverage of
  the `domain` and `application` packages. Docker must be running: the component tests start a
  MongoDB container. No test needs Kafka to be running.

## Project layout

```text
src/notifications/
  main.py            Composition root: settings, MongoDB client, consumer manager, lifespan
  api/               FastAPI routers and response models (read-only HTTP surface)
  messaging/         Kafka consumers and the background consumer manager
  application/       Application services and the repository / probe Protocols they use
  domain/            Business logic in plain Python (no FastAPI, aiokafka, pymongo or Pydantic)
  infrastructure/    Settings, MongoDB client, persistence documents, versioned index module
tests/
  unit/              Domain and application in isolation; API with fakes; no containers
  component/         The full application against a real MongoDB container
  support/           Fakes and the polling helper (no fixed sleeps in tests)
docs/adr/            Architecture Decision Records
```

Layering: Messaging and API → Application service → Domain → Repository. Consumers and routers
only do I/O and call one application service method.
