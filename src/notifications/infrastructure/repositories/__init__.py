"""MongoDB adapters for the application ports.

Each module maps domain objects to persistence documents explicitly (Principle I): UUIDs are stored
as canonical strings, amounts as ``Decimal128`` and timestamps as BSON dates in UTC. Every write is
idempotent on its own, because the shared MongoDB has no multi-document transactions (ADR 0002).
"""
