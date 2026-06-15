"""Push notifications on agent-state edges (issue #70).

Public surface for the notification subsystem: the broker, the channel contract,
and the concrete channels. The broker subscribes to the existing
``ActivityService`` bus and adds no new status computation — a notification is a
debounced rising edge into a notifiable state. Build it with
``NotificationBroker.from_config(cfg.notifications)`` (``None`` when off).

Off by default at the config layer (``cfg.notifications.enabled``).
"""

from __future__ import annotations

from grove.core.notifications.broker import NotificationBroker
from grove.core.notifications.channel import Notification, NotificationChannel
from grove.core.notifications.gotify import GotifyNotificationChannel
from grove.core.notifications.webhook import WebhookNotificationChannel

__all__ = [
    "GotifyNotificationChannel",
    "Notification",
    "NotificationBroker",
    "NotificationChannel",
    "WebhookNotificationChannel",
]
