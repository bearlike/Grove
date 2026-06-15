"""The Gotify notification channel — the first concrete sink (#70).

Gotify (https://github.com/gotify/server) is a self-hosted push server; the
maintainer already runs one, so it is the first channel. One HTTP call per
notification: ``POST {server_url}/message`` with the app token in the
``X-Gotify-Key`` header and a JSON body. A deep link rides Gotify's
``client::notification.click`` extra so tapping the phone notification opens the
workspace.

The HTTP boundary follows the ``MewboClient`` pattern exactly: the config holds
the *name* of the env var carrying the app token (never the secret — committed
config stays publishable), the value is read from the process environment at
construction, and ``transport`` is the ``httpx.MockTransport`` test seam. Errors
surface as the typed :class:`GotifyError`; httpx exceptions never leak past this
module, and the broker's best-effort guard turns any raise into a logged,
isolated failure.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from grove.core.config import GotifyChannelConfig
from grove.core.errors import GotifyError
from grove.core.notifications.channel import Notification, NotificationChannel


class GotifyNotificationChannel(NotificationChannel):
    """Pushes a :class:`Notification` to a Gotify server. ``name = "gotify"``."""

    name = "gotify"

    def __init__(
        self,
        cfg: GotifyChannelConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        token = os.environ.get(cfg.token_env, "")
        headers = {"X-Gotify-Key": token} if token else {}
        self._priority = cfg.priority
        self._http = httpx.Client(
            base_url=cfg.server_url.rstrip("/"),
            timeout=cfg.timeout_seconds,
            headers=headers,
            transport=transport,
        )

    def deliver(self, notification: Notification) -> None:
        """One ``POST /message``. Raises :class:`GotifyError` on any failure."""
        body: dict[str, Any] = {
            "title": notification.title(),
            "message": notification.body(),
            "priority": self._priority,
        }
        if notification.deep_link:
            # Gotify Android taps this URL; the desktop/web client honors it too.
            body["extras"] = {"client::notification": {"click": {"url": notification.deep_link}}}
        try:
            response = self._http.post("/message", json=body)
        except httpx.HTTPError as exc:
            raise GotifyError(f"gotify POST /message failed: {exc}") from exc
        if response.status_code >= 400:
            raise GotifyError(
                f"gotify POST /message returned {response.status_code}: {response.text[:200]}"
            )

    def close(self) -> None:
        self._http.close()


__all__ = ["GotifyNotificationChannel"]
