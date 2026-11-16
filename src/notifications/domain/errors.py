"""Typed domain errors (constitution Principle V)."""


class DomainError(Exception):
    """Base class for every business rule violation raised by the domain."""


class InvalidOrderTotalError(DomainError):
    """An order total is negative or not a finite number."""


class NaiveTimestampError(DomainError):
    """A timestamp has no timezone; the domain only accepts timezone-aware UTC times."""
