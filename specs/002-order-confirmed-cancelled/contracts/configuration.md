# Configuration contract (changes in US-02)

All US-01 variables are unchanged (see `specs/001-order-received-notification/contracts/configuration.md`).

## New in US-02

| Variable | Default | Validation | Meaning |
|---|---|---|---|
| `KAFKA_TOPIC_ORDER_PLACED` | `orders.order-placed` | non-empty | Topic of `OrderPlaced` events (`ORDER_RECEIVED`). |
| `KAFKA_TOPIC_ORDER_CONFIRMED` | `orders.order-confirmed` | non-empty | Topic of `OrderConfirmed` events (`ORDER_CONFIRMED`). |
| `KAFKA_TOPIC_ORDER_CANCELLED` | `orders.order-cancelled` | non-empty | Topic of `OrderCancelled` events (`ORDER_CANCELLED`). |

- The three names must differ. An invalid or duplicate value stops the service at startup with a
  validation error naming the variable.
- The defaults are the contract names in `ecommerce-infra/docs/events.md`. Override them only for
  tests or a renamed environment, never to change the contract.
- Each topic is one entry of the consumer's routing table. The one consumer, in group
  `KAFKA_GROUP_ID`, subscribes to all three.
