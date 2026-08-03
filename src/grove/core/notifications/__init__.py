"""Push notifications on workspace edges.

Public surface for the notification subsystem: the broker, the notification
event, the channel contract, and the concrete channels. The broker subscribes to
the existing ``ActivityService`` bus and adds no new status computation — a
notification is an edge in what already flows past it: an agent state, a fresh
question, or a workspace lifecycle event. Build it with
``NotificationBroker.from_config(cfg.notifications)`` (``None`` when off).

Off by default at the config layer (``cfg.notifications.enabled``).
"""

from __future__ import annotations

from grove.core.notifications.broker import NotificationBroker
from grove.core.notifications.channel import (
    Notification,
    NotificationChannel,
    NotificationReason,
    NotificationSeverity,
    NotificationTrigger,
    WorkspaceIdentity,
)
from grove.core.notifications.gotify import GotifyNotificationChannel
from grove.core.notifications.webhook import WebhookNotificationChannel

__all__ = [
    "GotifyNotificationChannel",
    "Notification",
    "NotificationBroker",
    "NotificationChannel",
    "NotificationReason",
    "NotificationSeverity",
    "NotificationTrigger",
    "WebhookNotificationChannel",
    "WorkspaceIdentity",
]
