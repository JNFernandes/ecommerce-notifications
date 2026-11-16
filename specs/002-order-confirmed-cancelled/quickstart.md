# Quickstart: validate US-02 (order confirmed and order cancelled)

This guide proves the feature against the shared local infrastructure. The automated proof is the
quality gate; these steps are the manual check of the same scenarios. Payload rules are in
[contracts/orders-order-confirmed-cancelled.consumer.md](contracts/orders-order-confirmed-cancelled.consumer.md),
and the message rules in [data-model.md](data-model.md).

## Prerequisites

- `ecommerce-infra` is running (`docker compose up -d` in `../ecommerce-infra`).
- `MONGODB_URI` points at the Docker MongoDB. On a machine where another MongoDB uses
  `127.0.0.1:27017`, use `127.0.0.2` (README → Troubleshooting).
- The service is running: `uv run uvicorn notifications.main:create_app --factory --port 8000`.
  `curl.exe http://localhost:8000/health/ready` returns `kafka: CONNECTED`.

The commands below are for PowerShell. Two helpers:

```powershell
function Send-Event($topic, $json) { $json | docker exec -i ecommerce-kafka-1 kafka-console-producer --bootstrap-server kafka:29092 --topic $topic }
function Query($js) { docker exec ecommerce-mongo-1 mongosh --quiet -u mongo_user -p mongo_pass --authenticationDatabase admin notifications --eval $js }
```

## 1. Automated gate (must be green)

```powershell
uv run ruff check; uv run ruff format --check; uv run mypy; uv run pytest --cov
```

## 2. Confirmation (User Story 1)

```powershell
Send-Event orders.order-confirmed '{"eventId":"6a1b2c3d-0000-4000-8000-000000000201","aggregateId":"0d1e2f3a-0000-4000-8000-000000000201","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc"}'
Query "printjson(db.notifications.find({orderId:'0d1e2f3a-0000-4000-8000-000000000201'}).toArray())"
```

Expected:
- One `ORDER_CONFIRMED` notification with the message
  `Your order 0d1e2f3a-0000-4000-8000-000000000201 was confirmed.`
- One `Simulated email sent … type=ORDER_CONFIRMED` line in the service log.
- No order context is created for this order, since its placement was never sent.

## 3. Cancellation with and without a reason (User Story 2)

```powershell
Send-Event orders.order-cancelled '{"eventId":"6a1b2c3d-0000-4000-8000-000000000202","aggregateId":"0d1e2f3a-0000-4000-8000-000000000202","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc","reason":"payment failed: amount exceeds the threshold"}'
Send-Event orders.order-cancelled '{"eventId":"6a1b2c3d-0000-4000-8000-000000000203","aggregateId":"0d1e2f3a-0000-4000-8000-000000000203","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc"}'
Query "printjson(db.notifications.find({type:'ORDER_CANCELLED'},{_id:0,orderId:1,message:1}).toArray())"
```

Expected messages:
- `Your order …0202 was cancelled: payment failed: amount exceeds the threshold.`
- `Your order …0203 was cancelled.`

## 4. Redelivery and invalid message (User Story 3)

1. Send the step 2 event again. There is still one notification for `…0201`, the log says
   `Event already recorded: type=ORDER_CONFIRMED`, and there is no second send line.
2. Send an invalid cancellation:

   ```powershell
   Send-Event orders.order-cancelled '{"eventId":"6a1b2c3d-0000-4000-8000-000000000204","aggregateId":"0d1e2f3a-0000-4000-8000-000000000204","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc","reason":42}'
   Query "printjson(db.inbound_dead_letters.find({topic:'orders.order-cancelled'},{_id:0,errorMessage:1,attempts:1}).toArray())"
   ```

   Expected: one dead letter with `reason: Value error, must be a JSON string or null` and
   `attempts: 1`. No notification for `…0204`.

## 5. One consumer for all topics

- `curl.exe http://localhost:8000/health/ready` reports one `kafka` state.
- The startup log shows a single "Kafka consumer connected" line.

## Cleanup

```powershell
Query "db.notifications.deleteMany({orderId:/^0d1e2f3a-0000-4000-8000-0000000002/}); db.inbound_dead_letters.deleteMany({payload:/0d1e2f3a-0000-4000-8000-0000000002/})"
```
