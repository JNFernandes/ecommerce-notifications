"""The step every use case shares: store a notification, then send it unless it is a duplicate."""

import logging
from enum import StrEnum

from notifications.application.ports import NotificationRepository, NotificationSender
from notifications.domain.notification import Notification

logger = logging.getLogger("notifications.application")


class RecordOutcome(StrEnum):
    """Result of recording a notification for an event."""

    RECORDED = "RECORDED"
    """A new notification was stored and sent."""
    ALREADY_RECORDED = "ALREADY_RECORDED"
    """The event was processed before; nothing new was stored or sent."""


async def record_and_send(
    notification: Notification,
    notifications: NotificationRepository,
    sender: NotificationSender,
) -> RecordOutcome:
    """Store ``notification``, then send it; a redelivered event changes nothing.

    The send (simulated) happens only after the notification is stored, and never for a
    duplicate source event (constitution Principle III, ADR 0002).

    Raises:
        Exception: any repository failure, unchanged, so that the consumer can retry.
    """
    if not await notifications.add(notification):
        logger.info(
            "Event already recorded: type=%s sourceEvent=%s order=%s",
            notification.type,
            notification.source_event_id,
            notification.order_id,
        )
        return RecordOutcome.ALREADY_RECORDED
    sender.send(notification)
    return RecordOutcome.RECORDED
