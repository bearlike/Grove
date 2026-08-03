"""The generic webhook notification channel — the second sink.

A plain JSON ``POST`` to a user-supplied URL. This is the "mechanism, not
policy" channel: it carries the notification's fields as a documented JSON
document so any consumer (a home-automation hook, a Slack relay, an ntfy topic)
can shape it. ntfy's JSON-publish API is supported directly — set ``topic`` and
point ``url`` at the ntfy base, and the body's ``topic`` / ``title`` /
``message`` / ``click`` / ``tags`` / ``priority`` keys are exactly what ntfy
expects.

The body carries **both** renderings (``message`` plain, ``markdown`` rich) and
the structured facts behind them (``severity``, ``trigger``, the open
``questions`` with their options) — a relay picks what it can use. That is the
whole point of the generic sink: it never has to re-derive a phrase or a
priority, and a consumer that wants to *answer* a question has the question id.

Same boundary discipline as the Gotify channel: optional bearer token by env-var
*name* (never the secret), ``httpx.MockTransport`` test seam, typed
:class:`WebhookError`, no httpx leakage.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from grove.core.agents import AgentQuestion
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
        self._priorities = dict(cfg.priorities)
        token = os.environ.get(cfg.token_env, "") if cfg.token_env else ""
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._http = httpx.Client(timeout=cfg.timeout_seconds, headers=headers, transport=transport)

    def deliver(self, notification: Notification) -> None:
        """One JSON ``POST``. Raises :class:`WebhookError` on any failure."""
        workspace = notification.workspace
        body: dict[str, Any] = {
            "title": notification.title(),
            "message": notification.body(),
            "markdown": notification.markdown(),
            # ntfy's own scale is 1-5, so the severity is mapped through config
            # rather than shipping the state string in an integer field.
            "priority": self._priorities.get(notification.severity, 3),
            "tags": list(notification.tags()),
            "trigger": notification.trigger,
            "event": notification.event,
            "severity": notification.severity,
            "workspace_id": workspace.workspace_id,
            "repo": workspace.repo_name,
            "branch": workspace.branch,
            "agent": workspace.agent_name,
            "state": notification.state.value if notification.state else None,
            "questions": [self._question_json(q) for q in notification.questions],
            "occurred_at": notification.occurred_at.isoformat(),
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

    @staticmethod
    def _question_json(question: AgentQuestion) -> dict[str, Any]:
        """One open question as JSON — the prompt and its options, for a relay
        that wants to render (or answer) the question rather than a prose body."""
        return {
            "id": question.id,
            "kind": question.kind,
            "prompt": question.prompt,
            "header": question.header,
            "multiselect": question.multiselect,
            "options": [
                {"label": opt.label, "description": opt.description} for opt in question.options
            ],
        }


__all__ = ["WebhookNotificationChannel"]
