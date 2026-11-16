"""Timestamp rules shared by the aggregates."""

from datetime import UTC, datetime

from notifications.domain.errors import NaiveTimestampError


def require_utc_millis(value: datetime) -> datetime:
    """Return ``value`` in UTC, truncated to milliseconds.

    MongoDB stores dates with millisecond precision, so truncating here keeps the value held in
    memory equal to the stored one.

    Raises:
        NaiveTimestampError: if ``value`` has no timezone.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveTimestampError(f"timestamp without timezone: {value.isoformat()}")
    utc = value.astimezone(UTC)
    return utc.replace(microsecond=utc.microsecond // 1000 * 1000)
