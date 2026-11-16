"""Typed domain errors (constitution Principle V)."""


class DomainError(Exception):
    """Base class for every business rule violation raised by the domain."""


class InvalidAmountError(DomainError):
    """An amount of money is negative or not a finite number."""


class InvalidOrderTotalError(InvalidAmountError):
    """An order total is negative or not a finite number."""


class InvalidCurrencyError(DomainError):
    """A currency is not a three-letter uppercase ISO 4217 code."""


class NaiveTimestampError(DomainError):
    """A timestamp has no timezone; the domain only accepts timezone-aware UTC times."""
