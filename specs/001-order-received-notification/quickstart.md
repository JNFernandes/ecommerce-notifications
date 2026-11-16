# Quickstart: validate US-01 (order received notification)

This guide proves the feature end to end against the shared local infrastructure. The automated
proof is the quality gate. The steps below are the manual check of the same scenarios.

Field rules are in [contracts/orders-order-placed.consumer.md](contracts/orders-order-placed.consumer.md),
documents in [data-model.md](data-model.md) and log lines in
[contracts/observability.md](contracts/observability.md).

## Prerequisites

- Docker running; `ecommerce-infra` started (`docker compose up -d` in `../ecommerce-infra`).
  The containers used below are `ecommerce-kafka-1` and `ecommerce-mongo-1`.
- `uv sync` done in this repository.

## 1. Automated gate (must be green)

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest --cov
```

Expected: all four pass. `pytest` starts MongoDB and Kafka containers. The component tests cover
scenarios 3 to 6 below with a real broker.

## 2. Start the service

```sh
uv run uvicorn notifications.main:create_app --factory --port 8000
curl -s http://localhost:8000/health/ready
```

Expected: `{"status":"UP","mongodb":"UP","kafka":"CONNECTED"}` within a few seconds, and an INFO
log line saying the consumer connected.

Helpers used below (Git Bash or any POSIX shell):

```sh
produce() { docker exec -i ecommerce-kafka-1 kafka-console-producer --bootstrap-server kafka:29092 --topic orders.order-placed; }
mongo()   { docker exec ecommerce-mongo-1 mongosh --quiet -u mongo_user -p mongo_pass --authenticationDatabase admin notifications --eval "$1"; }
```

## 3. Happy path (User Stories 1 and 3)

```sh
echo '{"eventId":"6a1b2c3d-0000-4000-8000-000000000001","aggregateId":"0d1e2f3a-0000-4000-8000-0000000000aa","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc","items":[],"totalAmount":129.9}' | produce
mongo 'printjson(db.notifications.find({orderId:"0d1e2f3a-0000-4000-8000-0000000000aa"}).toArray())'
mongo 'printjson(db.order_context.findOne({_id:"0d1e2f3a-0000-4000-8000-0000000000aa"}))'
```

Expected:
- One notification with type `ORDER_RECEIVED`, channel `email`, status `SENT` and the message
  `Your order 0d1e2f3a-0000-4000-8000-0000000000aa for 129.90 was received.`
- One order context with `totalAmount` `Decimal128("129.9")`.
- In the service log, one `Simulated email sent: …` line.

## 4. Redelivery (User Story 2)

Send the exact same line again, then:

```sh
mongo 'db.notifications.countDocuments({sourceEventId:"6a1b2c3d-0000-4000-8000-000000000001"})'
```

Expected: `1`. The log shows `already recorded`, with no second `Simulated email sent` line and
no dead letter.

## 5. Invalid message (User Story 4)

```sh
echo 'not json' | produce
echo '{"eventId":"6a1b2c3d-0000-4000-8000-000000000002","aggregateId":"0d1e2f3a-0000-4000-8000-0000000000bb","occurredAt":"2026-11-16T10:00:00Z","version":1,"totalAmount":10}' | produce
mongo 'printjson(db.inbound_dead_letters.find({},{payload:1,errorType:1,errorMessage:1,attempts:1}).toArray())'
```

Expected:
- Two dead letters with `attempts: 1`:
  - `not json`: `InvalidMessageError`, invalid JSON.
  - The second message: `customerId` missing.
- No notification for order `…bb`.
- Two ERROR log lines with topic, partition and offset.
- A valid event sent afterwards is processed normally.

## 6. Retries and hold (User Story 4)

```sh
docker pause ecommerce-mongo-1
echo '{"eventId":"6a1b2c3d-0000-4000-8000-000000000003","aggregateId":"0d1e2f3a-0000-4000-8000-0000000000cc","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc","totalAmount":5}' | produce
# watch the log for ~20 s, then:
docker unpause ecommerce-mongo-1
```

Expected:
1. WARNING lines `retrying … attempt=2/5`, then `3/5`, `4/5` and `5/5`.
2. Storing the dead letter also fails, so an ERROR line says
   `Dead-letter store unavailable, holding message`.
3. Readiness answers 503 with `mongodb: DOWN`.
4. After the unpause, the message is processed in the next round. It ends as a notification, not
   a dead letter, because MongoDB is back; nothing was skipped.

## 7. Kafka outage (User Story 5)

```sh
docker pause ecommerce-kafka-1
curl -s http://localhost:8000/health/ready   # 200, kafka DISCONNECTED (within ~20 s)
docker unpause ecommerce-kafka-1
curl -s http://localhost:8000/health/ready   # kafka CONNECTED again, without a restart
```

Also start the service while Kafka is paused. Startup completes, `/health/live` answers `UP`, and
readiness is 200 with `kafka` set to `CONNECTING` or `DISCONNECTED`.

## Cleanup

```sh
mongo 'db.notifications.deleteMany({}); db.order_context.deleteMany({}); db.inbound_dead_letters.deleteMany({})'
```
