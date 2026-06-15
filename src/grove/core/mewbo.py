"""All Mewbo REST API I/O Grove ever performs, bound to one configuration.

The dedicated side-effect module for the Mewbo orchestrator (#36) — the HTTP
sibling of ``git.py`` and ``tmux.py``. :class:`MewboClient` is the canonical
surface: every method wraps one endpoint, returns plain parsed dicts, and the
consumers (``MewboAdapter``, the manager's launch fork) stay pure over those
payloads. No Mewbo internals are ever imported; Grove talks only to the wire
(epic #34's boundary rule).

Auth follows the env-ref pattern: config carries the NAME of the environment
variable (``cfg.api_key_env``), never the key itself, and the value is read
from the consuming process's environment at construction. Failures surface as
the typed :class:`MewboError`; httpx exceptions never leak past this module.

Deliberately absent: ``/message`` and ``/interrupt`` wrappers — the follow-up
/steer operation is issue #37's surface and lands on its branch, not here.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger

from grove.core.config import MewboConfig
from grove.core.errors import MewboError


class MewboClient:
    """Sync HTTP client for the Mewbo session API, one instance per config.

    Wire facts (verified against the Mewbo console client + API guide,
    2026-06-11): auth is the ``X-API-KEY`` header; errors come back as an
    ``{"error": {code, reason}}`` envelope; ``POST /api/sessions`` returns
    ``{"session_id": ...}`` (server-minted — the opposite of Claude Code's
    client-minted ``--session-id``); ``cwd`` anchoring on create is additive
    and gated server-side by ``api.allow_external_cwd`` (Assistant #91).

    ``transport`` is the test seam: ``httpx.MockTransport`` fakes the wire
    without monkey-patching, matching the "stub only I/O boundaries" rule.
    """

    def __init__(
        self,
        cfg: MewboConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        api_key = os.environ.get(cfg.api_key_env, "")
        headers = {"X-API-KEY": api_key} if api_key else {}
        self._http = httpx.Client(
            base_url=cfg.base_url,
            timeout=cfg.timeout_seconds,
            headers=headers,
            transport=transport,
        )

    # ─── session lifecycle ──────────────────────────────────────────────────

    def create_session(
        self, *, cwd: str | None = None, title: str | None = None, model: str | None = None
    ) -> str:
        """Create a remote session; return the SERVER-minted session id.

        ``cwd`` anchors the session to an external worktree (top-level field,
        Assistant #91) — the API validates the directory exists, so callers
        create the worktree first. The title is set with a follow-up PATCH
        (the create endpoint takes none) and is deliberately best-effort: a
        label must never fail an already-created session.

        ``model`` (per-create model selection, #98) is forwarded on the create
        body when set — the Mewbo equivalent of ``--model`` for the CLI kinds.
        Unlike claude_code/codex it cannot ride a launch flag (mewbo runs the
        model server-side), so the only place to forward it is session-create.
        Provider boundary: the id is opaque — Grove forwards it verbatim and
        never interprets it. If the server doesn't honor a ``model`` field it is
        simply ignored (an extra create-body key is harmless), so this stays a
        best-effort forward, never a hard dependency on a specific API shape.
        """
        body: dict[str, Any] = {}
        if cwd is not None:
            body["cwd"] = cwd
        if model:
            body["model"] = model
        payload = self._request("POST", "/api/sessions", json_body=body)
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise MewboError("mewbo created a session but returned no session_id")
        if title:
            try:
                self._request(
                    "PATCH",
                    f"/api/sessions/{session_id}/title",
                    json_body={"title": title},
                )
            except MewboError as exc:
                logger.debug("could not set mewbo session title: {}", exc)
        return session_id

    def query(
        self,
        session_id: str,
        query: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Enqueue a run on the session (``POST /query``); return the response payload."""
        body: dict[str, Any] = {"query": query}
        if context:
            body["context"] = context
        return self._request("POST", f"/api/sessions/{session_id}/query", json_body=body)

    def send_message(self, session_id: str, text: str) -> None:
        """Steer the session's active run, or re-engage an idle/finished one.

        ``POST /message`` enqueues into a live run (202) or starts a fresh run
        with ``text`` as its query (200) — only a terminated session rejects
        (Devin-modeled semantics). Grove ignores the returned run id: run
        handles are Mewbo-internal, Grove tracks the session.
        """
        self._request("POST", f"/api/sessions/{session_id}/message", json_body={"text": text})

    def interrupt(self, session_id: str) -> None:
        """Interrupt the session's current tool step (idle → server-side no-op)."""
        self._request("POST", f"/api/sessions/{session_id}/interrupt")

    def archive(self, session_id: str) -> None:
        self._request("POST", f"/api/sessions/{session_id}/archive")

    def unarchive(self, session_id: str) -> None:
        self._request("DELETE", f"/api/sessions/{session_id}/archive")

    # ─── reads ──────────────────────────────────────────────────────────────

    def list_sessions(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        """Every session summary row (``GET /api/sessions`` unwraps ``{"sessions": [...]}``)."""
        params = {"include_archived": "1"} if include_archived else None
        payload = self._request("GET", "/api/sessions", params=params)
        sessions = payload.get("sessions")
        if not isinstance(sessions, list):
            return []
        return [row for row in sessions if isinstance(row, dict)]

    def events(self, session_id: str, *, after: str | None = None) -> dict[str, Any]:
        """The session's event log + authoritative envelope.

        Returns the whole payload: ``events`` (``{ts, type, payload}`` rows)
        plus the top-level ``status`` / ``done_reason`` / ``title`` /
        ``running`` the API computes server-side — the adapter reads those
        instead of reconstructing state from the timeline tail.
        """
        params = {"after": after} if after else None
        return self._request("GET", f"/api/sessions/{session_id}/events", params=params)

    def agents_tree(self, session_id: str) -> dict[str, Any]:
        """The sub-agent tree + token rollup (``GET /agents``).

        ``total_input_tokens`` here is PEAK semantics (root peak + per-sub-agent
        peaks — the context-pressure number); the cumulative billed sum is a
        different field on a different endpoint (``/usage``'s
        ``total_input_tokens_billed``). Never mix the two (epic #34 / Mewbo #45).
        """
        return self._request("GET", f"/api/sessions/{session_id}/agents")

    def close(self) -> None:
        self._http.close()

    # ─── internal ───────────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """One wire round-trip; every failure mode narrows to MewboError."""
        try:
            response = self._http.request(method, path, json=json_body, params=params)
        except httpx.HTTPError as exc:  # timeout, connect failure, protocol error
            raise MewboError(f"mewbo {method} {path} failed: {exc}") from exc
        if response.status_code >= 400:
            raise MewboError(
                f"mewbo {method} {path} returned {response.status_code}: "
                f"{self._error_reason(response)}"
            )
        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise MewboError(f"mewbo {method} {path} returned malformed JSON") from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _error_reason(response: httpx.Response) -> str:
        """Best-effort human reason from the ``{"error": {code, reason}}`` envelope."""
        try:
            data = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("reason"), str):
                return str(error["reason"])
            if isinstance(data.get("message"), str):
                return str(data["message"])
        return response.text[:200]


__all__ = ["MewboClient"]
