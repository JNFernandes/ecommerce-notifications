# Quickstart: validate US-04 (read API)

The contract is in [contracts/http-api.md](contracts/http-api.md).

## Prerequisites

- `ecommerce-infra` is running; `MONGODB_URI` is on `127.0.0.2` (README → Troubleshooting).
- The service is running on port 8000. Some notifications exist, for example from the US-01 to US-03
  quickstarts, or from an order placed through the Orders API.

## 1. Automated gate

```powershell
uv run ruff check; uv run ruff format --check; uv run mypy; uv run pytest --cov
```

## 2. List and page (User Story 1)

```powershell
curl.exe -s "http://localhost:8000/notifications?limit=2"
curl.exe -s "http://localhost:8000/notifications?limit=2&offset=2"
curl.exe -s "http://localhost:8000/notifications?type=PAYMENT_REFUNDED"
curl.exe -s "http://localhost:8000/notifications?customerId=3fa85f64-5717-4562-b3fc-2c963f66afa6&type=ORDER_CANCELLED"
curl.exe -s "http://localhost:8000/notifications?orderId=00000000-0000-4000-8000-000000000000"
```

Expected:
- Newest first, with `total` independent of `limit`/`offset`, and no item on both pages.
- Filters combine with AND.
- The last call returns `{"items":[],"total":0,"limit":20,"offset":0}`.

## 3. Fetch one (User Story 2)

Take an `id` from step 2:
- `curl.exe -s http://localhost:8000/notifications/<id>` returns the notification.
- With a random well-formed UUID, it returns 404 `{"detail":"notification <uuid> not found"}`.

## 4. Bad input (User Story 3)

```powershell
curl.exe -s -o NUL -w "%{http_code}\n" "http://localhost:8000/notifications?limit=0"     # 422
curl.exe -s -o NUL -w "%{http_code}\n" "http://localhost:8000/notifications?type=FOO"    # 422
curl.exe -s -o NUL -w "%{http_code}\n" "http://localhost:8000/notifications/not-a-uuid"  # 422
curl.exe -s -o NUL -w "%{http_code}\n" -X DELETE "http://localhost:8000/notifications/<id>"  # 405
```

## 5. Docs, Kafka down, CORS (User Story 4)

- Open `http://localhost:8000/docs`: both endpoints, with every parameter and field described.
- Run `docker pause ecommerce-kafka-1`. Step 2 still answers the same. Then
  `docker unpause ecommerce-kafka-1`.
- Start the service with `$env:CORS_ALLOWED_ORIGINS = 'http://localhost:5173'`, then:

  ```powershell
  curl.exe -s -D - -o NUL -H "Origin: http://localhost:5173" http://localhost:8000/notifications
  ```

  The response headers include `access-control-allow-origin: http://localhost:5173`. With
  `Origin: http://evil.example` that header is absent.
