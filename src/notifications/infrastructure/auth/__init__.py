"""Access token adapters: the ecommerce-identity JWKS key store and the JWT verifier.

Tokens are verified locally with the public keys Identity publishes (ADR 0004). Keys are fetched
lazily and cached, so Identity's availability never affects startup, readiness or Kafka.
"""
