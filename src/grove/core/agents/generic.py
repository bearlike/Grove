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
    AgentMessage,
    FinalResult,
    OrderedDigest,
    QueuedMessage,
    SessionControls,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TodoList,
)


class GenericAdapter:
    """No-op :class:`AgentAdapter` for tools with no known transcript format."""

    kind = "generic"
    remote = False
    resumable = False
    # No queue: a busy shell prompt buffers keystrokes in the terminal and
    # records nothing, so there is nothing Grove could read.
    reports_queue = False
    # `read_messages` is empty by construction, so there is no tool result to
    # carry a flag — a third distinct reason for `False`, not a degradation.
    reports_tool_errors = False

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

    def tool_version(self, command: str) -> str | None:
        # A generic command is a shell or an unknown tool: there is no version
        # flag Grove can assume, and probing an arbitrary binary with
        # ``--version`` is exactly the guessing the provider boundary forbids.
        del command
        return None

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

    def discover_all(self) -> tuple[SessionRef, ...]:
        # No known transcript format, so no store to enumerate — no-op.
        return ()

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        del cwd
        return []

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        # A bare shell records no conversation, so there is no spine to read —
        # the honest empty answer a content consumer acts on (it emits nothing).
        del cwd, session_id
        return ()

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

    def pending_queue(self, cwd: Path, session_id: str) -> tuple[QueuedMessage, ...]:
        # A bare shell has no queue: text typed at a busy prompt is buffered by
        # the terminal, and nothing records it. An ANSWER, not a debt.
        del cwd, session_id
        return ()

    def latest_task(self, cwd: Path, session_id: str) -> str | None:
        # No transcript format to read a task text from.
        del cwd, session_id
        return None

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        # A bare shell has no slash-command / skill / MCP surface to enumerate.
        del cwd, session_id
        return SessionControls.empty()
