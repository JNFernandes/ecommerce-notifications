# Configuration contract

Environment variables read by `notifications.infrastructure.settings.Settings`. Names are
case-insensitive. Every row below is documented in the README (FR-021).

## Existing (bootstrap), unchanged

| Variable | Default |
|---|---|
| `MONGODB_URI` | `mongodb://mongo_user:mongo_pass@localhost:27017/?authSource=admin` |
| `MONGODB_DATABASE` | `notifications` |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` |
| `KAFKA_GROUP_ID` | `notifications-service` |
| `CORS_ALLOWED_ORIGINS` | empty |
| `LOG_LEVEL` | `INFO` |

## New in US-01

| Variable | Type | Default | Validation | Meaning |
|---|---|---|---|---|
| `CONSUMER_RETRY_MAX_ATTEMPTS` | int | `5` | `>= 1` | Attempts per message, the first included. `1` means no retries. |
| `CONSUMER_RETRY_BASE_DELAY_SECONDS` | float | `1.0` | `> 0` | Pause before the second attempt. |
| `CONSUMER_RETRY_MULTIPLIER` | float | `2.0` | `>= 1` | Growth factor of the pause between attempts. |
| `CONSUMER_RETRY_MAX_DELAY_SECONDS` | float | `10.0` | `>= CONSUMER_RETRY_BASE_DELAY_SECONDS` | Cap on any single pause. Also used between rounds while the dead-letter store is unavailable. |

The pause before attempt `n` (n ≥ 2) is
`min(BASE_DELAY × MULTIPLIER^(n-2), MAX_DELAY)`. With the defaults the pauses are 1, 2, 4 and
8 s, about 15 s per failing message.

An invalid value stops the service at startup with a validation error that names the variable.
