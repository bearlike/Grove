"""Read-only client for one local OpenCode server.

The server is a fresher projection than its SQLite database while a turn is
running. This client maps only HTTP transport failures into ``None``; OpenCode
payloads remain raw dictionaries for the adapter (and a native owner) to
normalize at their own boundary.

**A server address is supplied, never guessed, and that is load-bearing rather
than fastidious.** ``opencode serve`` is launched with ``--port 0`` and prints
the port it was given, so no fixed port identifies a Grove-owned server; the
owner also mints a per-launch ``OPENCODE_SERVER_PASSWORD``, so an unauthenticated
read of one would answer 401 even if the port were right. A guessed address is
therefore wrong twice over — and its cost lands on the read path: an unreachable
bounded GET measured **45 ms** on this host, which the ~1 Hz activity tick would
pay per session per read, for an answer SQLite already holds. So
:meth:`for_reads` returns ``None`` unless an address is configured, and every
caller falls back to the database rather than dialling a hopeful port.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger


class OpenCodeClient:
    """Bounded read-only access to OpenCode's legacy local HTTP surface."""

    BASE_URL_ENV = "GROVE_OPENCODE_SERVER_URL"
    """Names a reachable OpenCode server. Absent means "read the database" —
    the ordinary case, since nothing in Grove publishes a server's address yet."""

    DEFAULT_TIMEOUT_SECONDS = 0.5

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client

    @classmethod
    def for_reads(cls) -> OpenCodeClient | None:
        """The configured client, or ``None`` when no server address is known."""
        base_url = os.environ.get(cls.BASE_URL_ENV, "").strip()
        return cls(base_url) if base_url else None

    def sessions(self, cwd: str) -> list[dict[str, Any]] | None:
        """List server sessions for one directory, or ``None`` when unreachable."""
        body = self._get("/session", cwd)
        if not isinstance(body, list):
            return None
        return [item for item in body if isinstance(item, dict)]

    def session(self, cwd: str, session_id: str) -> dict[str, Any] | None:
        """Return one server session, or ``None`` for an unavailable/missing read."""
        body = self._get(f"/session/{session_id}", cwd)
        return body if isinstance(body, dict) else None

    def messages(self, cwd: str, session_id: str) -> list[dict[str, Any]] | None:
        """Return one session's server-projected messages, oldest first."""
        body = self._get(f"/session/{session_id}/message", cwd)
        if not isinstance(body, list):
            return None
        return [item for item in body if isinstance(item, dict)]

    def children(self, cwd: str, session_id: str) -> list[dict[str, Any]] | None:
        """Return the server's immediate children for exactly this parent."""
        body = self._get(f"/session/{session_id}/children", cwd)
        if not isinstance(body, list):
            return None
        return [item for item in body if isinstance(item, dict)]

    def descendants(self, cwd: str, session_id: str) -> dict[str, str] | None:
        """Walk only server-declared child edges, returning ``child_id → parent_id``."""
        pending = [session_id]
        seen = {session_id}
        descendants: dict[str, str] = {}
        while pending:
            parent = pending.pop()
            children = self.children(cwd, parent)
            if children is None:
                return None
            for child in children:
                child_id = child.get("id")
                parent_id = child.get("parentID")
                if (
                    not isinstance(child_id, str)
                    or not child_id
                    or parent_id != parent
                    or child_id in seen
                ):
                    continue
                seen.add(child_id)
                descendants[child_id] = parent
                pending.append(child_id)
        return descendants

    def status(self, cwd: str) -> dict[str, dict[str, Any]] | None:
        """The server's live per-session statuses, when it publishes them."""
        body = self._get("/session/status", cwd)
        if not isinstance(body, dict):
            return None
        return {
            key: value
            for key, value in body.items()
            if isinstance(key, str) and isinstance(value, dict)
        }

    def providers(self, cwd: str) -> dict[str, Any] | None:
        """The provider/model catalog, used only after CLI discovery is absent."""
        body = self._get("/provider", cwd)
        return body if isinstance(body, dict) else None

    def _get(self, path: str, cwd: str) -> object | None:
        try:
            if self._client is not None:
                response = self._client.get(path, params={"directory": cwd})
            else:
                with httpx.Client(base_url=self._base_url, timeout=self._timeout_seconds) as client:
                    response = client.get(path, params={"directory": cwd})
            response.raise_for_status()
            body: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("OpenCode HTTP read {} failed: {}", path, exc)
            return None
        return body
