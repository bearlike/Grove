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
from grove.core.agents.model import AgentActivityState, AgentQuestion

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

# Events that END a pending question's life (#109). A question tool's own
# PostToolUse means it was answered; any other tool's PreToolUse, a new prompt,
# a turn Stop (which also fires on Esc-cancel), or session end all mean the
# question is gone. ``Notification`` / ``SessionStart`` are deliberately absent:
# a permission ``Notification`` fires ~6s AFTER the ask while the question is
# still on screen, so it must PRESERVE (carry forward) the standing capture,
# never clear it.
_QUESTION_CLEAR_EVENTS: Final[frozenset[str]] = frozenset(
    {"PostToolUse", "UserPromptSubmit", "Stop", "SessionEnd"}
)

# How long a WORKING push is trusted with no newer signal. Only WORKING ages out
# (the dead-agent guard: a session killed right after PreToolUse must not pin
# WORKING forever). Settled pushes (WAITING/BLOCKED/IDLE) never age out — they
# stay true until the transcript moves, and BLOCKED is precisely the state
# polling cannot see, so expiring it into a polled guess re-creates the
# "permission prompt shows as working" bug it exists to fix.
DEFAULT_SIDECAR_MAX_AGE_SECONDS: Final = 300


@dataclass(slots=True, frozen=True)
class PendingQuestion:
    """A question captured live from a PreToolUse hook, before the transcript flushes it.

    Claude Code writes nothing to the JSONL while an ``AskUserQuestion`` is on
    screen, so the ask-time hook is the *only* signal (#109). ``tool_name`` +
    ``tool_input`` are the raw hook payload, kept verbatim and normalized to
    ``AgentQuestion``(s) at read time through the shared
    :meth:`AgentQuestion.from_tool_call` seam — no question shape is re-derived
    here. ``tool_use_id`` is the group answer-back address: the key the answer
    endpoint matches on and the key the transcript's resolving ``tool_result``
    carries once the human answers (or Esc-cancels).
    """

    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]
    asked_at: datetime

    def to_json(self) -> dict[str, Any]:
        return {
            "tool_use_id": self.tool_use_id,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "asked_at": self.asked_at.isoformat(),
        }

    @classmethod
    def from_json(cls, data: object) -> PendingQuestion | None:
        """Parse a captured question; ``None`` on anything malformed (best-effort)."""
        if not isinstance(data, dict):
            return None
        try:
            asked_at = datetime.fromisoformat(str(data["asked_at"]))
        except (KeyError, ValueError, TypeError):
            return None
        if asked_at.tzinfo is None:
            return None  # aware-only, same rule as HookRecord.ts
        tool_use_id = data.get("tool_use_id")
        tool_name = data.get("tool_name")
        tool_input = data.get("tool_input")
        if (
            not (isinstance(tool_use_id, str) and tool_use_id)
            or not (isinstance(tool_name, str) and tool_name)
            or not isinstance(tool_input, dict)
        ):
            return None
        return cls(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            tool_input=tool_input,
            asked_at=asked_at,
        )


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
    # A structured question the agent is asking right now, captured at ask-time
    # (#109). ``None`` whenever no question stands. It rides the same sidecar as
    # the pushed state so the one file the ActivityService already reads carries
    # both the live status AND the live question.
    question: PendingQuestion | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "event": self.event,
            "cwd": self.cwd,
            "transcript_path": self.transcript_path,
            "tmux_pane": self.tmux_pane,
            "ts": self.ts.isoformat(),
            "question": self.question.to_json() if self.question is not None else None,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> HookRecord | None:
        """Parse a sidecar; return ``None`` on anything malformed (best-effort)."""
        try:
            ts = datetime.fromisoformat(data["ts"])
            if ts.tzinfo is None:
                # Grove always writes aware UTC; a naive ts is foreign/corrupt.
                # Rejecting HERE keeps the aware-vs-naive TypeError out of
                # supersedes_poll's comparisons, where it would escape the
                # malformed-handling boundary and break the whole snapshot.
                return None
            return cls(
                session_id=str(data["session_id"]),
                state=AgentActivityState(data["state"]),
                event=str(data.get("event", "")),
                cwd=_opt_str(data.get("cwd")),
                transcript_path=_opt_str(data.get("transcript_path")),
                tmux_pane=_opt_str(data.get("tmux_pane")),
                ts=ts,
                # A malformed question is dropped without failing the whole
                # record — the status half of the sidecar still stands.
                question=PendingQuestion.from_json(data.get("question")),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def supersedes_poll(
        self,
        *,
        now: datetime,
        transcript_at: datetime | None,
        max_age_seconds: int = DEFAULT_SIDECAR_MAX_AGE_SECONDS,
    ) -> bool:
        """Whether this push still outranks the polled blend.

        The push describes one moment; the transcript is the ground truth that
        moves past it. Two rules, in order:

        - **Transcript outran the push → defer to polled.** A ``Stop`` (WAITING)
          followed by a steer that wrote new transcript records is stale even
          seconds later — wall-clock age is the wrong staleness axis (the old
          age-only rule pinned WAITING on a re-engaged agent for 5 minutes).
        - **WORKING ages out; settled states never do.** A WORKING push with no
          newer signal eventually means a dead agent (kill mid-tool), so the
          poller takes over after ``max_age_seconds``. WAITING/BLOCKED/IDLE stay
          authoritative however old: nothing happened since, so they are still
          true — and BLOCKED is invisible to polling entirely.
        """
        if transcript_at is not None and transcript_at > self.ts:
            return False
        if self.state is AgentActivityState.WORKING:
            age = (now - self.ts).total_seconds()
            return 0 <= age <= max_age_seconds
        return True


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
            question=cls._pending_question(
                payload, event, session_id=session_id, sidecar_dir=sidecar_dir, now=now
            ),
        )
        cls.write(record, sidecar_dir=sidecar_dir)
        return record

    @classmethod
    def _pending_question(
        cls,
        payload: dict[str, Any],
        event: str,
        *,
        session_id: str,
        sidecar_dir: Path,
        now: datetime,
    ) -> PendingQuestion | None:
        """The pending question this event leaves standing (#109).

        The lifecycle is a small state machine over the single per-session
        sidecar:

        - a question-tool ``PreToolUse`` CAPTURES a fresh question (the ask);
        - any other ``PreToolUse``, or a :data:`_QUESTION_CLEAR_EVENTS` event,
          CLEARS it (a different tool ran, or the turn/session moved on — a
          question-tool ``PostToolUse`` is the answered case, ``Stop`` the
          Esc-cancel case);
        - every other event (``Notification``, ``SessionStart``) CARRIES the
          standing capture FORWARD, so the ~6s-later permission ``Notification``
          doesn't erase a question that is still on screen.

        The carry-forward reads the prior sidecar; a missing/corrupt one just
        means "nothing was pending", which is the correct default.
        """
        if event == "PreToolUse":
            tool_name = payload.get("tool_name")
            if not (isinstance(tool_name, str) and AgentQuestion.recognizes(tool_name)):
                return None  # a non-question tool starting clears any pending ask
            tool_use_id = payload.get("tool_use_id")
            tool_input = payload.get("tool_input")
            if not (isinstance(tool_use_id, str) and tool_use_id) or not isinstance(
                tool_input, dict
            ):
                return None  # malformed question payload → nothing to capture
            return PendingQuestion(
                tool_use_id=tool_use_id,
                tool_name=tool_name,
                tool_input=tool_input,
                asked_at=now,
            )
        if event in _QUESTION_CLEAR_EVENTS:
            return None
        prior = cls.read(session_id, sidecar_dir=sidecar_dir)
        return prior.question if prior is not None else None

    @staticmethod
    def write(record: HookRecord, *, sidecar_dir: Path) -> None:
        """Atomically write a session's sidecar. Best-effort (never raises)."""
        try:
            paths.ensure_dir(sidecar_dir)
            target = sidecar_dir / f"{record.session_id}.json"
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record.to_json()), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.debug("could not write agent sidecar for {}: {}", record.session_id, exc)

    @staticmethod
    def read(session_id: str, *, sidecar_dir: Path) -> HookRecord | None:
        """Read a session's sidecar, or ``None`` if missing or malformed.

        Pure mechanism — whether the push still outranks the polled blend is the
        record's own call (:meth:`HookRecord.supersedes_poll`), because that
        judgment needs the transcript's clock, which only the blend site has.
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
        return HookRecord.from_json(data)

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
