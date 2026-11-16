# Configuration contract (changes in US-03)

All earlier variables are unchanged (US-01 and US-02 `contracts/configuration.md`).

| Variable | Default | Validation | Meaning |
|---|---|---|---|
| `KAFKA_TOPIC_PAYMENT_REFUNDED` | `payments.payment-refunded` | non-empty; differs from the three `KAFKA_TOPIC_ORDER_*` names | Topic of `PaymentRefunded` events (`PAYMENT_REFUNDED` notifications). One more route of the same consumer. |

- No refund-specific retry settings. A missing order context uses `CONSUMER_RETRY_*`: with the
  defaults, about 15 s and 5 attempts before the dead letter.
- Like the order topics, the default is the contract name. Override it only for tests or a renamed
  environment.
