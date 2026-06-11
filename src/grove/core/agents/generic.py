"""The null adapter for agents Grove can't introspect (a plain shell, etc.).

Every method is benign: no launch decoration, no transcripts, an ``UNKNOWN``
activity. It exists so the dashboard treats a ``kind:"generic"`` agent uniformly
— a card with no metrics rather than a special-case branch in the service.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    OrderedDigest,
    SessionSummary,
    SessionTurn,
)


class GenericAdapter:
    """No-op :class:`AgentAdapter` for tools with no known transcript format."""

    kind = "generic"

    def launch_decoration(self, session_id: str) -> list[str]:
        del session_id
        return []

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        del cwd, session_id
        return []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        del cwd, exclude_id
        return []

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        del cwd
        return []

    def read_turns(
        self, paths: Sequence[Path], *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        del paths, last
        return ()

    def parse_activity(self, paths: Sequence[Path]) -> AgentActivity:
        del paths
        return AgentActivity.empty(AgentActivityState.UNKNOWN)

    def transcript_digest(self, paths: Sequence[Path]) -> OrderedDigest:
        del paths
        return OrderedDigest()
