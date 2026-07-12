"""The null adapter for agents Grove can't introspect (a plain shell, etc.).

Every method is benign: no launch decoration, no transcripts, an ``UNKNOWN``
activity. It exists so the dashboard treats a ``kind:"generic"`` agent uniformly
— a card with no metrics rather than a special-case branch in the service.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from grove.core.agents.model import (
    AgentActivity,
    AgentActivityState,
    FinalResult,
    OrderedDigest,
    SessionControls,
    SessionSummary,
    SessionTurn,
    TodoList,
)


class GenericAdapter:
    """No-op :class:`AgentAdapter` for tools with no known transcript format."""

    kind = "generic"
    remote = False
    resumable = False

    def launch_decoration(self, session_id: str, *, resume: bool = False) -> list[str]:
        del session_id, resume
        return []

    def model_decoration(self, model: str) -> list[str]:
        # A bare shell has no model concept — ignore the request, run as named.
        del model
        return []

    def offline_decoration(self) -> list[str]:
        # A bare shell has no tool concept, so no flag to gate — no-op.
        return []

    def telemetry_env(self) -> dict[str, str]:
        # A bare shell has no telemetry to enable — no-op.
        return {}

    def available_models(self, command: str) -> tuple[str, ...]:
        # No model concept, so nothing to offer a picker.
        del command
        return ()

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        del cwd, session_id
        return []

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        del cwd, exclude_id
        return []

    def discover_births(
        self, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, datetime | None, float]]:
        del cwd, exclude_id
        return []

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        del cwd
        return []

    def read_turns(
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        del cwd, session_id, last
        return ()

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        del cwd, session_id
        return AgentActivity.empty(AgentActivityState.UNKNOWN)

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        del cwd, session_id
        return OrderedDigest()

    def final_result(self, cwd: Path, session_id: str) -> FinalResult | None:
        # No transcript format to project a terminal outcome from.
        del cwd, session_id
        return None

    def latest_todo(self, cwd: Path, session_id: str) -> TodoList | None:
        # No transcript format to project a todo/checklist state from.
        del cwd, session_id
        return None

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        # A bare shell has no slash-command / skill / MCP surface to enumerate.
        del cwd, session_id
        return SessionControls.empty()
