"""Which Langfuse PROJECT the configured credentials belong to.

A Langfuse deep link is ``{host}/project/{project_id}/sessions/{session_id}``,
and Grove knows the host (config) and the session (`WorkspaceState.telemetry_
session_id`) but not the project: a key pair belongs to exactly one project and
nothing in the config names it. `GET /api/public/projects` answers for the
credentials themselves, so the id is DERIVED from what is already configured
rather than becoming a knob an operator has to keep in sync with their keys —
the same reason `derive_env` composes the OTLP headers instead of storing them.

Cached and best-effort in the `ReleaseChecker` shape, for the same reasons: a
render path must never stall on somebody else's HTTP, and a project id changes
essentially never. A failure keeps the last-good answer and is retried once per
TTL, never per request.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from loguru import logger

_TIMEOUT_SECONDS: Final = 5.0
# A project id is effectively immutable for a key pair, so an hour is short.
_DEFAULT_TTL: Final = timedelta(hours=6)


def fetch_project_id(host: str, auth_header: str) -> str | None:
    """The project the credentials in ``auth_header`` belong to, or ``None``.

    ``auth_header`` is the ready-made ``Basic …`` value `derive_env` already
    composes for the OTLP exporter, so this never handles the key pair itself.
    A key pair scopes to ONE project, so a response naming several is a shape
    Grove does not understand and must not guess at: it answers ``None`` rather
    than picking the first, because a link into the wrong project is worse than
    no link.
    """
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/public/projects",
        headers={"Authorization": auth_header, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read() or b"{}")
    projects = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(projects, list) or len(projects) != 1:
        return None
    first = projects[0]
    project_id = first.get("id") if isinstance(first, dict) else None
    return project_id if isinstance(project_id, str) and project_id else None


@dataclass(slots=True)
class LangfuseProject:
    """Cached, best-effort resolution of the configured credentials' project id.

    ``resolve`` never raises: an unreachable Langfuse, a 401 from rotated keys
    or an unexpected payload all yield the last-good id (or ``None``), and the
    caller renders no link rather than a broken one.
    """

    ttl: timedelta = _DEFAULT_TTL
    # Injectable for tests; ``None`` → the real fetch, resolved at CALL time so a
    # monkeypatch of the module seam is honored (the `ReleaseChecker` rule).
    fetcher: Callable[[str, str], str | None] | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    _project_id: str | None = field(default=None, init=False)
    _key: tuple[str, str] | None = field(default=None, init=False)
    _checked_at: datetime | None = field(default=None, init=False)

    def resolve(self, host: str, auth_header: str) -> str | None:
        """The project id for these credentials, refreshed once per TTL.

        Keyed by ``(host, auth_header)`` so rotating a key pair or repointing
        the host re-resolves immediately instead of serving the previous
        deployment's project for the rest of the TTL — the cache is a rate
        limit on one question, never a memory of a different one.
        """
        key = (host, auth_header)
        if key != self._key:
            self._key, self._project_id, self._checked_at = key, None, None
        if self._checked_at is not None and self.clock() - self._checked_at < self.ttl:
            return self._project_id
        try:
            fetch = self.fetcher if self.fetcher is not None else fetch_project_id
            resolved = fetch(host, auth_header)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            logger.debug("langfuse project lookup failed for {}: {}", host, exc)
            self._checked_at = self.clock()
            return self._project_id
        if resolved is not None:
            self._project_id = resolved
        self._checked_at = self.clock()
        return self._project_id


__all__ = ["LangfuseProject", "fetch_project_id"]
