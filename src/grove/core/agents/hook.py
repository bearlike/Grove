"""Grove-managed Claude Code status hook — the push side of agent status (#18).

Polling `stop_reason` + tmux activity (the MVP, #14) cannot cleanly separate
*waiting-for-you* from *done*, and cannot see a permission prompt at all. Claude
Code **hooks** push exact lifecycle events; the Grove hook turns each into a tiny
sidecar file the ``ActivityService`` reads to *override* the polled status.

This is opt-in and degrades gracefully: with no hook installed there is no
sidecar and the polled blend stands unchanged. Robust by design — unlike peer
tools that string-match the CLI's prompt copy (which breaks when Anthropic
rewords it), the hook event names are a stable contract.

Three atomic pieces live here:

- :class:`HookRecord` — one session's pushed status (the on-disk shape).
- :class:`ClaudeHook` — the pure event→state mapping + the read/write/install
  mechanism. Stateless; all methods are static/class methods over the record.
- the rendered settings dict (``ClaudeHook.settings``) Grove passes to
  ``claude --settings`` so the hook installs *without* touching the user's own
  ``.claude/settings.json`` (uninstall = stop passing the flag).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Final

from loguru import logger

from grove.core import paths
from grove.core.agents.model import AgentActivityState

# Hook event names Claude Code emits → the agent state they imply. ``Notification``
# is the high-value one: it fires for "needs your permission" / "waiting for your
# input", which polling can't see → BLOCKED. ``SubagentStop`` maps to nothing so a
# finishing sub-agent never flips the main thread's state. Unknown events no-op.
_STATE_BY_EVENT: Final[dict[str, AgentActivityState]] = {
    "SessionStart": AgentActivityState.WORKING,
    "UserPromptSubmit": AgentActivityState.WORKING,
    "PreToolUse": AgentActivityState.WORKING,
    "PostToolUse": AgentActivityState.WORKING,
    "Notification": AgentActivityState.BLOCKED,
    "Stop": AgentActivityState.WAITING,
    "SessionEnd": AgentActivityState.IDLE,
}

# How long a sidecar's push state is trusted before the poller takes back over.
# Hooks fire on every lifecycle event, so a fresh session keeps this current; a
# stale sidecar means the session went quiet and the polled blend should win.
DEFAULT_SIDECAR_MAX_AGE_SECONDS: Final = 300


@dataclass(slots=True, frozen=True)
class HookRecord:
    """One session's pushed status — the agent-sidecar on-disk shape."""

    session_id: str
    state: AgentActivityState
    event: str
    cwd: str | None
    transcript_path: str | None
    tmux_pane: str | None
    ts: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "event": self.event,
            "cwd": self.cwd,
            "transcript_path": self.transcript_path,
            "tmux_pane": self.tmux_pane,
            "ts": self.ts.isoformat(),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HookRecord | None:
        """Parse a sidecar; return ``None`` on anything malformed (best-effort)."""
        try:
            return cls(
                session_id=str(data["session_id"]),
                state=AgentActivityState(data["state"]),
                event=str(data.get("event", "")),
                cwd=_opt_str(data.get("cwd")),
                transcript_path=_opt_str(data.get("transcript_path")),
                tmux_pane=_opt_str(data.get("tmux_pane")),
                ts=datetime.fromisoformat(data["ts"]),
            )
        except (KeyError, ValueError, TypeError):
            return None


class ClaudeHook:
    """Pure event→state mapping plus the sidecar read/write/install mechanism."""

    # The CLI entry point Claude Code invokes (see `grove agent-hook`). The hook
    # reads its JSON on stdin and writes a sidecar; the same command serves every
    # session because the payload carries the session id.
    COMMAND: ClassVar[str] = "grove agent-hook"

    @staticmethod
    def state_for(event_name: str, payload: dict[str, Any]) -> AgentActivityState | None:
        """Map a hook event to an agent state, or ``None`` for events we ignore.

        ``payload`` is accepted for future refinement (e.g. distinguishing a
        permission ``Notification`` from an idle one); today the event name is
        sufficient and the extra signal is reserved.
        """
        del payload
        return _STATE_BY_EVENT.get(event_name)

    @classmethod
    def record_event(
        cls,
        payload: dict[str, Any],
        *,
        sidecar_dir: Path,
        tmux_pane: str | None,
        now: datetime,
    ) -> HookRecord | None:
        """Turn one hook payload into a sidecar write. ``None`` if the event is ignored.

        Best-effort: a malformed payload or unwritable dir is logged and swallowed
        — a hook must never break the agent it instruments.
        """
        event = str(payload.get("hook_event_name", ""))
        session_id = payload.get("session_id")
        state = cls.state_for(event, payload)
        if state is None or not isinstance(session_id, str) or not session_id:
            return None
        record = HookRecord(
            session_id=session_id,
            state=state,
            event=event,
            cwd=_opt_str(payload.get("cwd")),
            transcript_path=_opt_str(payload.get("transcript_path")),
            tmux_pane=tmux_pane,
            ts=now,
        )
        cls.write(record, sidecar_dir=sidecar_dir)
        return record

    @staticmethod
    def write(record: HookRecord, *, sidecar_dir: Path) -> None:
        """Atomically write a session's sidecar. Best-effort (never raises)."""
        try:
            sidecar_dir.mkdir(parents=True, exist_ok=True)
            target = sidecar_dir / f"{record.session_id}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record.to_json()), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.debug("could not write agent sidecar for {}: {}", record.session_id, exc)

    @staticmethod
    def read(
        session_id: str,
        *,
        sidecar_dir: Path,
        now: datetime,
        max_age_seconds: int = DEFAULT_SIDECAR_MAX_AGE_SECONDS,
    ) -> HookRecord | None:
        """Read a session's sidecar, or ``None`` if missing, malformed, or stale.

        Staleness is the fallback contract: an old sidecar means the push signal
        went quiet, so the caller should defer to the polled status instead.
        """
        path = sidecar_dir / f"{session_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("could not read agent sidecar {}: {}", path, exc)
            return None
        if not isinstance(data, dict):
            return None
        record = HookRecord.from_json(data)
        if record is None:
            return None
        age = (now - record.ts).total_seconds()
        if age < 0 or age > max_age_seconds:
            return None
        return record

    @staticmethod
    def settings(command: str = COMMAND) -> dict[str, Any]:
        """The Claude Code settings dict that installs the Grove hook on every event.

        Hook-only: Grove writes this to its own file and passes it via
        ``claude --settings``, so the user's ``.claude/settings.json`` is never
        touched. Every tracked event routes to the one ``command`` (it reads the
        session id from stdin), so a single entry per event covers all sessions.
        """
        hook_entry = [{"hooks": [{"type": "command", "command": command}]}]
        return {"hooks": dict.fromkeys(_STATE_BY_EVENT, hook_entry)}


def run_hook_from_stdin() -> int:
    """CLI edge for ``grove agent-hook``: read one hook payload, write the sidecar.

    Always returns 0 — a hook must never fail the agent it instruments, so a
    malformed payload or an ignored event is a silent no-op. ``$TMUX_PANE`` is
    read here (the edge) and threaded into the record so a future pane-targeting
    consumer can map a session to its window without a second tmux call.
    """
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    ClaudeHook.record_event(
        payload,
        sidecar_dir=paths.agent_sidecar_dir(),
        tmux_pane=_opt_str(os.environ.get("TMUX_PANE")),
        now=datetime.now(tz=UTC),
    )
    return 0


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
