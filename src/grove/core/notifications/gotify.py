"""The Gotify notification channel — the first concrete sink.

Gotify (https://github.com/gotify/server) is a self-hosted push server. One HTTP
call per notification: ``POST {server_url}/message`` with the *application* token
in the ``X-Gotify-Key`` header and a JSON body of ``title`` / ``message`` /
``priority`` / ``extras``.

Three facts about Gotify shape this channel, all verified against the server's
OpenAPI spec and the Android client's source (see CLAUDE.md for the full notes):

- **Markdown is the rich surface.** ``extras["client::display"].contentType =
  "text/markdown"`` makes both official clients render CommonMark (Android via
  Markwon, web via react-markdown). HTML is never rendered, so the notification's
  ``markdown()`` body is pure markdown.
- **Priority is the urgency dial**, and the Android client bins it: ``>=8`` is a
  heads-up notification with sound, ``4-7`` vibrates, ``1-3`` is silent. The
  severity → priority map lives in config so a user re-tunes it without a code
  change; a pending question lands at 8, a routine pause at 2.
- **An app token can only send.** Deleting or superseding a message needs a
  *client* token, and there is no update endpoint at all — so "clear the earlier
  needs-input push once the agent moves on" is not implementable against this
  API, and we do not fake it.

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
        self._priorities = dict(cfg.priorities)
        self._fallback_priority = cfg.priority
        self._markdown = cfg.markdown
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
            "message": notification.markdown() if self._markdown else notification.body(),
            "priority": self._priority_for(notification),
            "extras": self._extras(notification),
        }
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

    # ─── internal ────────────────────────────────────────────────────────────

    def _priority_for(self, notification: Notification) -> int:
        """Severity → Gotify's 0-10 dial, per config. Unmapped → the fallback."""
        return self._priorities.get(notification.severity, self._fallback_priority)

    def _extras(self, notification: Notification) -> dict[str, Any]:
        """Gotify's ``extras`` map — only the keys the official clients honor.

        ``client::display.contentType`` switches on markdown rendering (both
        clients). ``client::notification.click.url`` is what the Android client
        opens on tap; the web client ignores it, which is why the deep link is
        *also* rendered inside the markdown body — one link, two ways to reach it.
        """
        extras: dict[str, Any] = {}
        if self._markdown:
            extras["client::display"] = {"contentType": "text/markdown"}
        if notification.deep_link:
            extras["client::notification"] = {"click": {"url": notification.deep_link}}
        return extras


__all__ = ["GotifyNotificationChannel"]
