"""Simulated notification sending: one log line per stored notification (no email provider)."""

import logging

from notifications.domain.notification import Notification

send_logger = logging.getLogger("notifications.send")


class LoggingNotificationSender:
    """``NotificationSender`` that writes the simulated send to the ``notifications.send`` log."""

    def send(self, notification: Notification) -> None:
        """Log the simulated email send with the notification ids."""
        send_logger.info(
            "Simulated email sent: notification=%s type=%s channel=%s customer=%s order=%s "
            "sourceEvent=%s",
            notification.id,
            notification.type,
            notification.channel,
            notification.customer_id,
            notification.order_id,
            notification.source_event_id,
        )
