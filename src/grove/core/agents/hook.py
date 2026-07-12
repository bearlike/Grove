"""Grove-managed Claude Code status hook — the push side of agent status (#18).

Polling `stop_reason` + tmux activity (the MVP, #14) cannot cleanly separate
*waiting-for-you* from *done*, and cannot see a permission prompt at all. Claude
Code **hooks** push exact lifecycle events; the Grove hook turns each into a tiny
sidecar file the ``ActivityService`` reads to *override* the polled status.

This is on by default (#171; opt-in through #18) and degrades gracefully: with
no hook installed there is no sidecar and the polled blend stands unchanged.
Robust by design — unlike peer tools that string-match the CLI's prompt copy
(which breaks when Anthropic rewords it), the hook event names are a stable
contract.

Four atomic pieces live here:

- :class:`HookRecord` — one session's pushed status (the on-disk shape; the
  offline ground truth `ActivityService` always reads first).
- :class:`ClaudeHook` — the pure event→state mapping + the read/write/install
  mechanism. Stateless; all methods are static/class methods over the record.
- the rendered settings dict (``ClaudeHook.settings``) Grove passes to
  ``claude --settings`` so the hook installs *without* touching the user's own
  ``.claude/settings.json`` (uninstall = stop passing the flag).
- the daemon **PUSH path** (#171): every registered event now ALSO carries a
  native Claude Code ``{"type": "http"}`` handler that POSTs straight to the
  daemon's ingest route (`grove.daemon.app`), so the dashboard refreshes the
  instant the hook fires instead of waiting out the ~2s poll tick. It never
  replaces the sidecar file — that stays the offline truth a restarted daemon
  (or a client with no live connection) still reads correctly.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final

from loguru import logger

from grove.core import paths
from grove.core.agents.model import AgentActivityState, AgentQuestion

if TYPE_CHECKING:
    # Annotation-only (postponed annotations): the adoption seam composes
    # `WorkspaceState.adopts_session` with sidecar evidence, but importing the
    # engine dataclass at runtime would point the agents layer back at the
    # engine. The method only calls `state.adopts_session` (duck-typed), so the
    # TYPE_CHECKING import keeps the dependency direction clean.
    from grove.core.workspace import WorkspaceState

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

# Registered ALONGSIDE the state-mapped events (#171) purely so the daemon PUSH
# path (below) fires on sub-agent lifecycle too — a finishing sub-agent still
# changes the transcript (worth an immediate refresh) even though it never
# flips the MAIN thread's state (`state_for` returns `None` for both; the
# sidecar-write side is unaffected).
_SUBAGENT_EVENTS: Final[tuple[str, ...]] = ("SubagentStart", "SubagentStop")

# The three sub-kinds Claude Code's ``Notification`` event covers: a tool
# permission ask, the "still there?" idle nudge, and the general
# needs-your-input case. `state_for` keys off the bare event name regardless
# (all three collapse to BLOCKED — the one polling-invisible signal, #18);
# this tuple only widens the *registration* in `settings()` so each is its own
# matcher entry rather than one untyped catch-all (#171).
_NOTIFICATION_MATCHERS: Final[tuple[str, ...]] = (
    "permission_prompt",
    "idle_prompt",
    "agent_needs_input",
)

# The daemon route this module's http hook entries POST to (#171). A module
# constant, not a literal repeated in both `settings()` and `grove.daemon.app`
# — the daemon imports it to register the exact same path so the two can't
# drift.
HOOK_INGEST_ROUTE: Final = "/hooks/agent-events"

# The daemon binds loopback-only on this port by default (`grove daemon serve`,
# `client/backend.py::BackendConfig.daemon_port` — same literal, not a new
# policy). A custom `--port` breaks this guess; that's fine, the http push is
# best-effort on top of the sidecar file, never the only path to a correct
# status.
DEFAULT_DAEMON_LOOPBACK_URL: Final = "http://127.0.0.1:7421"

# Sibling of the hook-only settings file (same directory, same lifecycle: both
# are (re)written by `_ensure_hook_settings` on every launch). NOT the
# `SessionStore` pairing bearer every other daemon route trusts — that needs a
# human to click approve, and this hook fires dozens of times per session with
# nobody watching, so `ClaudeHook.ensure_ingest_token` mints and shares a
# same-host secret file instead.
_INGEST_TOKEN_FILENAME: Final = "hook-ingest.token"


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

    @classmethod
    def adopts(
        cls,
        state: WorkspaceState,
        born_at: datetime | None,
        *,
        candidate: HookRecord | None,
        reference_pane: str | None,
        cwd: Path,
    ) -> bool:
        """Whether ``state`` adopts a discovered session — the ONE evidence seam.

        Composes the two axes `WorkspaceState.adopts_session` weighs, so the
        composition can't drift between the two discovery sites
        (`ActivityService.sessions_for`, `SessionExplorer.for_workspace`, #F10a):

        - transcript BIRTH (``born_at``, immutable) — the pure predicate's job;
        - a hook sidecar proving the session was live *in this workspace* — the
          boundary's job, distilled here from the pre-read ``candidate`` sidecar
          via :meth:`live_here_at`.

        Takes the already-read ``candidate`` record and the workspace's
        ``reference_pane`` (both resolved once per session per tick by the
        caller) so no sidecar is re-read (#F9). ``reference_pane`` is the
        ``tmux_pane`` recorded on the *minted* session's sidecar — the pane this
        workspace owns; passing ``None`` (no minted id, no sidecar yet, or a
        sidecar with no pane) drops the live-here arm entirely, leaving
        birth-only adoption. See :meth:`live_here_at` for why the pane check is
        load-bearing.
        """
        live_at = cls.live_here_at(candidate, cwd=cwd, reference_pane=reference_pane)
        return state.adopts_session(born_at, live_here_at=live_at)

    @classmethod
    def live_here_at(
        cls, record: HookRecord | None, *, cwd: Path, reference_pane: str | None
    ) -> datetime | None:
        """The sidecar ts proving ``record``'s session was live *here*, or ``None``.

        Adoption evidence for a session the user RESUMED inside a workspace's
        pane (#117): its transcript is born before the workspace, so birth can't
        adopt it (`WorkspaceState.adopts_session`), but a hook sidecar recorded
        from the workspace's own pane *after* creation can.

        Attribution is **pane-verified** (#F1): the candidate sidecar's
        ``tmux_pane`` must equal the workspace's ``reference_pane`` (the pane the
        minted session's sidecar recorded). cwd-match alone was a cross-tenant
        hole — a fresh workspace at a shared cwd (ROOT placement, whose cwd is
        the repo root) would adopt a *different* live workspace's session, since
        that tenant's sidecar keeps refreshing ``ts >= created_at`` with the same
        cwd. The pane is the identity that a shared cwd cannot forge. The cwd
        match is retained as defense-in-depth, and the caller's ``>= created_at``
        guard still rejects a previous tenant of a reused pane whose sidecar
        predates the workspace.

        Returns the *ts*, not a verdict: the ``>= created_at`` comparison is
        `adopts_session`'s single gate, so this stays mechanism (attribute) and
        leaves policy (adopt) to the one predicate. Takes a pre-read record
        (never reads a sidecar) so the caller controls the one-read-per-tick
        discipline (#F9).
        """
        if record is None or record.cwd is None or reference_pane is None:
            return None
        if record.tmux_pane != reference_pane:
            return None
        return record.ts if Path(record.cwd).resolve() == cwd.resolve() else None

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
    def ensure_ingest_token() -> str:
        """Get-or-create the same-host secret the http hook and the daemon's
        ingest route both trust (#171).

        Deliberately NOT the multi-device `SessionStore` pairing bearer every
        other daemon route uses — pairing needs a human to approve a
        challenge, and this fires on every hook event with nobody watching.
        Persisted next to the hook-only settings file (same directory, same
        best-effort write discipline as `ClaudeHook.settings`/
        `WorkspaceManager._ensure_hook_settings`) so both the CLI-rendered
        settings and the daemon process resolve the identical value without
        threading it through any call site. An unwritable dir still returns a
        fresh in-memory token — the caller degrades to "this request's token
        won't match", never a raise.
        """
        path = paths.agent_hooks_settings_path().parent / _INGEST_TOKEN_FILENAME
        try:
            existing = path.read_text(encoding="utf-8").strip()
            if existing:
                return existing
        except OSError:
            pass
        token = secrets.token_urlsafe(32)
        try:
            paths.ensure_dir(path.parent)
            path.write_text(token, encoding="utf-8")
            path.chmod(0o600)
        except OSError as exc:
            logger.debug("could not persist hook ingest token: {}", exc)
        return token

    @staticmethod
    def settings(
        command: str = COMMAND, *, daemon_url: str | None = DEFAULT_DAEMON_LOOPBACK_URL
    ) -> dict[str, Any]:
        """The Claude Code settings dict that installs the Grove hook on every event.

        Hook-only: Grove writes this to its own file and passes it via
        ``claude --settings``, so the user's ``.claude/settings.json`` is never
        touched.

        Two independent handlers per event (#171): the ``command`` handler is
        unchanged since #18 (writes the offline-truth sidecar); ``daemon_url``
        (a mechanism default, not None) adds an ``http`` handler that POSTs the
        SAME event straight to the daemon's ingest route so the dashboard can
        refresh immediately instead of waiting out the poll tick. Claude Code
        dispatches every registered handler independently, so the http POST
        can never add latency to the command handler or the agent's own turn
        — the alternative (``grove agent-hook`` making the HTTP call itself,
        synchronously, before it exits) would. Pass ``daemon_url=None`` to get
        the pre-#171 command-only shape (the test seam).

        Registers a WIDER event set than the state map: `_SUBAGENT_EVENTS`
        never move the sidecar's state (`state_for` returns ``None`` for
        both — a sub-agent lifecycle never flips the main thread) but still
        deserve a push refresh, since a finishing sub-agent changes the
        transcript. ``Notification`` is split into its `_NOTIFICATION_MATCHERS`
        sub-kinds as separate matcher entries rather than one catch-all —
        `state_for` still keys off the bare event name (all three collapse to
        BLOCKED); the split is registration-only.
        """
        handlers: list[dict[str, Any]] = [{"type": "command", "command": command}]
        if daemon_url:
            handlers.append(
                {
                    "type": "http",
                    "url": f"{daemon_url}{HOOK_INGEST_ROUTE}",
                    "headers": {"Authorization": f"Bearer {ClaudeHook.ensure_ingest_token()}"},
                }
            )
        catch_all = [{"hooks": handlers}]
        hooks: dict[str, Any] = dict.fromkeys((*_STATE_BY_EVENT, *_SUBAGENT_EVENTS), catch_all)
        hooks["Notification"] = [
            {"matcher": matcher, "hooks": handlers} for matcher in _NOTIFICATION_MATCHERS
        ]
        return {"hooks": hooks}


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
