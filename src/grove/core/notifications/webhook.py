"""The generic webhook notification channel — the second sink (#70).

A plain JSON ``POST`` to a user-supplied URL. This is the "mechanism, not
policy" channel: it carries the notification's fields as a documented JSON
document so any consumer (a home-automation hook, a Slack relay, an ntfy topic)
can shape it. ntfy's JSON-publish API is supported directly — set ``topic`` and
point ``url`` at the ntfy base, and the body's ``topic`` / ``title`` /
``message`` / ``click`` / ``tags`` / ``priority`` keys are exactly what ntfy
expects.

Same boundary discipline as the Gotify channel: optional bearer token by env-var
*name* (never the secret), ``httpx.MockTransport`` test seam, typed
:class:`WebhookError`, no httpx leakage.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from grove.core.config import WebhookChannelConfig
from grove.core.errors import WebhookError
from grove.core.notifications.channel import Notification, NotificationChannel


class WebhookNotificationChannel(NotificationChannel):
    """POSTs a :class:`Notification` as JSON to a webhook URL. ``name = "webhook"``."""

    name = "webhook"

    def __init__(
        self,
        cfg: WebhookChannelConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._url = cfg.url
        self._topic = cfg.topic
        token = os.environ.get(cfg.token_env, "") if cfg.token_env else ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(timeout=cfg.timeout_seconds, headers=headers, transport=transport)

    def deliver(self, notification: Notification) -> None:
        """One JSON ``POST``. Raises :class:`WebhookError` on any failure."""
        body: dict[str, Any] = {
            "title": notification.title(),
            "message": notification.body(),
            "priority": notification.state.value,
            "tags": [notification.state.value],
            "workspace_id": notification.workspace_id,
            "repo": notification.repo_name,
            "branch": notification.branch,
            "agent": notification.agent_name,
            "state": notification.state.value,
        }
        if notification.deep_link:
            body["click"] = notification.deep_link  # ntfy field name; harmless elsewhere
        if self._topic:
            body["topic"] = self._topic  # ntfy JSON-publish requires it
        try:
            response = self._http.post(self._url, json=body)
        except httpx.HTTPError as exc:
            raise WebhookError(f"webhook POST {self._url} failed: {exc}") from exc
        if response.status_code >= 400:
            raise WebhookError(
                f"webhook POST {self._url} returned {response.status_code}: {response.text[:200]}"
            )

    def close(self) -> None:
        self._http.close()


__all__ = ["WebhookNotificationChannel"]
