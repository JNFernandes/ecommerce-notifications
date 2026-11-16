"""Messaging layer: Kafka consumers and the background consumer manager.

Consumers deserialize and validate inbound events, call exactly one application service method,
and commit offsets only after the MongoDB save. They contain no business logic and never
produce events.
"""
