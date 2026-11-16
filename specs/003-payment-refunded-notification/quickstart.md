# Quickstart: validate US-03 (payment refunded)

This guide proves the feature against the shared local infrastructure. Payload rules are in
[contracts/payments-payment-refunded.consumer.md](contracts/payments-payment-refunded.consumer.md).

## Prerequisites

- `ecommerce-infra` is running; `MONGODB_URI` points at the Docker MongoDB (`127.0.0.2` on a machine
  with another MongoDB on `127.0.0.1:27017`, see README → Troubleshooting).
- The service is running: `uv run uvicorn notifications.main:create_app --factory --port 8000`.
  Readiness shows `kafka: CONNECTED`.

PowerShell helpers:

```powershell
function Send-Event($topic, $json) { $json | docker exec -i ecommerce-kafka-1 kafka-console-producer --bootstrap-server kafka:29092 --topic $topic }
function Query($js) { docker exec ecommerce-mongo-1 mongosh --quiet -u mongo_user -p mongo_pass --authenticationDatabase admin notifications --eval $js }
```

## 1. Automated gate

```powershell
uv run ruff check; uv run ruff format --check; uv run mypy; uv run pytest --cov
```

## 2. Place, then refund (User Story 1)

```powershell
Send-Event orders.order-placed '{"eventId":"6a1b2c3d-0000-4000-8000-000000000400","aggregateId":"0d1e2f3a-0000-4000-8000-000000000401","occurredAt":"2026-11-16T10:00:00Z","version":1,"customerId":"c1c2c3c4-0000-4000-8000-0000000000cc","items":[],"totalAmount":129.9}'
# Wait until this prints 1 before sending the refund. The two topics are read independently;
# a refund that overtakes its placement would be retried and dead-lettered (README limitation).
Query "db.order_context.countDocuments({_id:'0d1e2f3a-0000-4000-8000-000000000401'})"
Send-Event payments.payment-refunded '{"eventId":"6a1b2c3d-0000-4000-8000-000000000401","occurredAt":"2026-11-16T10:05:00+00:00","aggregateId":"9a9a9a9a-0000-4000-8000-000000000401","version":1,"orderId":"0d1e2f3a-0000-4000-8000-000000000401","amount":129.90,"currency":"EUR","refundedAt":"2026-11-16T10:05:00+00:00"}'
Query "db.notifications.find({orderId:'0d1e2f3a-0000-4000-8000-000000000401'}).forEach(d=>print(d.type+' | '+d.customerId+' | '+d.message))"
```

Expected:
- `ORDER_RECEIVED`, and then
  `PAYMENT_REFUNDED | c1c2c3c4-…cc | Your payment of 129.90 EUR for order 0d1e2f3a-…0401 was refunded.`
- The order is the `orderId`, not the payment id `9a9a…`.
- One send log line `type=PAYMENT_REFUNDED`.

## 3. Refund without an order context (User Story 2)

```powershell
Send-Event payments.payment-refunded '{"eventId":"6a1b2c3d-0000-4000-8000-000000000402","occurredAt":"2026-11-16T10:05:00+00:00","aggregateId":"9a9a9a9a-0000-4000-8000-000000000402","version":1,"orderId":"0d1e2f3a-0000-4000-8000-000000000402","amount":5,"currency":"EUR","refundedAt":"2026-11-16T10:05:00+00:00"}'
```

The service log shows WARNING `Processing failed, retrying … attempt=2/5 … OrderContextNotFoundError`
up to `5/5`, about 15 s in total, then an ERROR `Message dead-lettered`. Then:

```powershell
Query "db.inbound_dead_letters.find({errorType:'OrderContextNotFoundError'},{_id:0,attempts:1,errorMessage:1}).toArray()"
```

Expected: `attempts: 5`, with the message `no order context for order 0d1e2f3a-…0402`, and no
notification for that order.

## 4. Redelivery and invalid refund (User Story 3)

1. Send the step 2 refund again. The log says `Event already recorded: type=PAYMENT_REFUNDED`, and
   there is still one refund notification.
2. Send the step 2 refund without `"currency":"EUR"` and with a new `eventId`. Expected: a dead
   letter with `attempts: 1` and `currency: Field required`.

## Cleanup

```powershell
Query "db.notifications.deleteMany({orderId:/^0d1e2f3a-0000-4000-8000-0000000004/}); db.order_context.deleteMany({_id:/^0d1e2f3a-0000-4000-8000-0000000004/}); db.inbound_dead_letters.deleteMany({payload:/0d1e2f3a-0000-4000-8000-0000000004/})"
```
