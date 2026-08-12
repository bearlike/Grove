"""Grove-managed Claude Code status hook — the push side of agent status.

Polling `stop_reason` + tmux activity cannot cleanly separate
*waiting-for-you* from *done*, and cannot see a permission prompt at all. Claude
Code **hooks** push exact lifecycle events; the Grove hook turns each into a tiny
sidecar file the ``ActivityService`` reads to *override* the polled status.

This is on by default and degrades gracefully: with
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
- the daemon **PUSH path**: after writing the sidecar the entry point
  POSTs the event to the daemon's ingest route (`grove.daemon.app`), so the
  dashboard refreshes the instant the hook fires instead of waiting out the ~2s
  poll tick. It never replaces the sidecar file — that stays the offline truth a
  restarted daemon (or a client with no live connection) still reads correctly.

The ``UserPromptSubmit`` hook also carries the ONE thing that travels the other
way — Claude Code injects that event's stdout into the model's context — which
is how the first-turn brief reaches the agent (:mod:`grove.core.agents.brief`).

**A containerized agent has none of this by default.** ``grove-agent-hook`` is
a console script of a package the project's own
image never installed, and the daemon's loopback is another namespace's
loopback — so both arms failed, loudly, in Claude's own UI, on every event. The
rendered command therefore probes for the entry point and falls back to spooling
the raw payload into a directory bind-mounted from the host, which
:meth:`ClaudeHook.drain` folds through the very same ``record_event``; and the
push moved INTO the entry point, so it is made exactly where it can work.
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
import sys
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Final

from loguru import logger

from grove.core import paths
from grove.core.agents.brief import AgentBrief
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

# Events that END a pending question's life. A question tool's own
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

# Registered ALONGSIDE the state-mapped events purely so the daemon PUSH
# path (below) fires on sub-agent lifecycle too — a finishing sub-agent still
# changes the transcript (worth an immediate refresh) even though it never
# flips the MAIN thread's state (`state_for` returns `None` for both; the
# sidecar-write side is unaffected).
_SUBAGENT_EVENTS: Final[tuple[str, ...]] = ("SubagentStart", "SubagentStop")

# Every hook event fired from INSIDE a sub-agent extends the SAME base payload
# every top-level event does, carrying `agent_id`/`agent_type` alongside it
# (Claude Code 2.1.227+; `SubagentStart`/`SubagentStop` declare both required,
# every other event populates them too). That is the whole seam: `record_event`
# below routes on `agent_id`'s presence rather than needing a second capture
# mechanism. This is the SUB-AGENT's own state map — deliberately separate from
# `_STATE_BY_EVENT`, which governs the MAIN thread's sidecar and must never see
# these entries (a sub-agent's `PreToolUse` is not evidence about what the main
# thread is doing). `Stop` is REWRITTEN to `SubagentStop` inside a sub-agent
# (Claude Code's own behavior), so a sub-agent's terminal event always arrives
# under that name — `Stop` itself never carries an `agent_id`.
_SUBAGENT_STATE_BY_EVENT: Final[dict[str, AgentActivityState]] = {
    "SubagentStart": AgentActivityState.WORKING,
    "PreToolUse": AgentActivityState.WORKING,
    "PostToolUse": AgentActivityState.WORKING,
    "Notification": AgentActivityState.BLOCKED,
    "SubagentStop": AgentActivityState.WAITING,
}

# The sub-agent sidecar tree is a CHILD of the top-level session's own sidecar
# dir (`<sidecar_dir>/subagents/<session_id>/<agent_id>.json`) — same reasoning
# as `agent_hook_spool_dir`: every caller already threads `sidecar_dir` through,
# so no second path needs plumbing to every read site.
_SUBAGENT_SIDECAR_DIR: Final = "subagents"

# Cap on a sub-agent's captured final message (`SubagentStop`'s
# `last_assistant_message`) — the same discipline `AgentActivity.current_task`
# applies on the ~1 Hz path, so one verbose sub-agent can't balloon its sidecar.
_LAST_MESSAGE_CAP: Final = 500

# The three sub-kinds Claude Code's ``Notification`` event covers: a tool
# permission ask, the "still there?" idle nudge, and the general
# needs-your-input case. `state_for` keys off the bare event name regardless
# (all three collapse to BLOCKED — the one polling-invisible signal);
# this tuple only widens the *registration* in `settings()` so each is its own
# matcher entry rather than one untyped catch-all.
_NOTIFICATION_MATCHERS: Final[tuple[str, ...]] = (
    "permission_prompt",
    "idle_prompt",
    "agent_needs_input",
)

# The daemon route this module's http hook entries POST to. A module
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

# How the rendered hook command hands the daemon address to the entry point.
# An argv, not a config read: this process runs on every hook event of every
# session in the fleet.
_DAEMON_URL_FLAG: Final = "--daemon-url"

# The push is an optimization over a sidecar that is already on disk, so it
# must never hold an agent's turn open waiting for a daemon that is busy or
# gone. Short enough to be invisible, long enough for a loopback round trip.
PUSH_TIMEOUT_SECONDS: Final = 2.0


@dataclass(slots=True, frozen=True)
class PendingQuestion:
    """A question captured live from a PreToolUse hook, before the transcript flushes it.

    Claude Code writes nothing to the JSONL while an ``AskUserQuestion`` is on
    screen, so the ask-time hook is the *only* signal. ``tool_name`` +
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
    # A structured question the agent is asking right now, captured at ask-time.
    # ``None`` whenever no question stands. It rides the same sidecar as
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


@dataclass(slots=True, frozen=True)
class SubagentHookRecord:
    """One sub-agent's pushed status — the hook's LIVE view, before any
    sidechain transcript has necessarily been flushed to disk.

    Keyed by ``(session_id, agent_id)`` rather than ``session_id`` alone: a
    top-level session fanning out several sub-agents at once emits several of
    these, one per agent, and none of them may overwrite the top-level
    session's own :class:`HookRecord` sidecar (the bug this whole record type
    exists to close — before it, a sub-agent's ``PreToolUse``/``PostToolUse``
    was keyed the same as the main thread's and silently corrupted the
    top-level push status while the sub-agent ran).

    ``current_tool`` is the name of a ``PreToolUse`` this sub-agent has not yet
    resolved with its OWN ``PostToolUse`` — the identical "unresolved means
    in-flight" rule :func:`grove.core.agents.model.tool_outcomes` applies to a
    finished transcript, read here directly off the live push instead of a
    transcript tail. ``state`` only ever settles to WAITING on an explicit
    ``SubagentStop`` (never inferred from silence): a real sub-agent measured
    113s with no sidecar growth mid-tool-call, so quiet must never read as
    finished.
    """

    session_id: str
    agent_id: str
    agent_type: str | None
    state: AgentActivityState
    event: str
    started_at: datetime
    last_event_at: datetime
    # The tool a PreToolUse started and no PostToolUse has yet resolved, or
    # ``None`` between calls (or before the first one). Not carried forward
    # across a resolving PostToolUse — it is a snapshot of "right now", not a
    # running log.
    current_tool: str | None = None
    # SubagentStop's `last_assistant_message`, truncated. `None` until the
    # sub-agent actually stops (never a placeholder while it runs).
    last_message: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "state": self.state.value,
            "event": self.event,
            "started_at": self.started_at.isoformat(),
            "last_event_at": self.last_event_at.isoformat(),
            "current_tool": self.current_tool,
            "last_message": self.last_message,
        }

    @classmethod
    def from_json(cls, data: object) -> SubagentHookRecord | None:
        """Parse a sub-agent sidecar; ``None`` on anything malformed (best-effort)."""
        if not isinstance(data, dict):
            return None
        try:
            started_at = datetime.fromisoformat(str(data["started_at"]))
            last_event_at = datetime.fromisoformat(str(data["last_event_at"]))
            state = AgentActivityState(data["state"])
        except (KeyError, ValueError, TypeError):
            return None
        if started_at.tzinfo is None or last_event_at.tzinfo is None:
            return None  # aware-only, same rule as HookRecord.ts
        session_id = data.get("session_id")
        agent_id = data.get("agent_id")
        if not (isinstance(session_id, str) and session_id) or not (
            isinstance(agent_id, str) and agent_id
        ):
            return None
        return cls(
            session_id=session_id,
            agent_id=agent_id,
            agent_type=_opt_str(data.get("agent_type")),
            state=state,
            event=str(data.get("event", "")),
            started_at=started_at,
            last_event_at=last_event_at,
            current_tool=_opt_str(data.get("current_tool")),
            last_message=_opt_str(data.get("last_message")),
        )


class ClaudeHook:
    """Pure event→state mapping plus the sidecar read/write/install mechanism."""

    # The console script Claude Code invokes. The hook reads its JSON on stdin
    # and writes a sidecar; the same command serves every session because the
    # payload carries the session id.
    #
    # A DEDICATED entry point, not `grove agent-hook`, because this is the
    # hottest process in the system: Claude spawns one per hook event, on nine
    # event types, for every session in the fleet, with no debouncing. Routed
    # through the `grove` script it paid for Typer + the daemon's FastAPI import
    # — ~1s of CPU and 60MB RSS to write one small JSON file, which at fleet
    # scale burned multiple cores. `grove-agent-hook` binds straight to
    # `run_hook_from_stdin`, so the process imports only this module's own
    # dependencies. The old subcommand stays registered for back-compat with
    # settings files written before this change.
    COMMAND: ClassVar[str] = "grove-agent-hook"

    #: Spool entries the drain has claimed but not yet folded, and the partial
    #: file the shell redirect is still writing, both stay OUT of ``*.json`` so a
    #: concurrent drain cannot pick up a half-written or already-owned payload.
    SPOOL_SUFFIX: ClassVar[str] = ".json"

    @classmethod
    def spool_script(cls, spool_dir: Path) -> str:
        """Shell that drops one hook payload into *spool_dir*, verbatim.

        **The whole point is that it carries no policy.** A containerized agent
        has no ``grove-agent-hook``: it is a console script of a Python package
        the project's own image never installed, so every hook fired
        ``/bin/sh: 1: grove-agent-hook: not found`` and every hook-driven
        feature — the sidecar, the status blend's push signal, the ask-time
        question capture — was dead for the whole workspace while the user was
        greeted by an error at each session start. The event→state map and the
        pending-question state machine stay HERE, in Python, run once on the
        host by :meth:`drain`; the container side only has to move bytes, which
        POSIX ``sh`` can do without a JSON parser, a runtime, or a copy of any
        rule that would then drift from this module.

        Deliberately no ``mkdir -p``: *spool_dir* is a bind mount the create
        path establishes, so a missing one means the mount is absent and the
        redirect fails LOUDLY in the agent's own hook output. Creating it would
        turn that into events written to a container-local directory that
        nothing ever reads — a recorder that cannot record, reported as
        healthy.

        The temp-then-rename is why the drain never sees a partial payload, and
        the filename only has to be unique: ordering comes from mtime, which is
        the event's own clock rather than the drain's.
        """
        target = shlex.quote(str(spool_dir))
        return (
            f'{{ __grove_f="{target}/$$-$(date +%s)"; '
            f'cat > "$__grove_f.tmp" && mv "$__grove_f.tmp" "$__grove_f{cls.SPOOL_SUFFIX}"; }}'
        )

    @classmethod
    def hook_command(cls, spool_dir: Path, *, daemon_url: str | None = None) -> str:
        """The ``command`` handler that works in EITHER namespace.

        ``grove-agent-hook`` when it is on ``PATH`` (every host launch — the
        dedicated entry point, unchanged and still the fast path), else
        the spool fallback. A capability probe, not a runtime branch: nothing
        here knows or asks whether it is in a container, which is what lets ONE
        settings file serve both and keeps the launch composition free of a
        second hook shape to keep in step.

        ``exec`` is load-bearing — it replaces the shell, so a non-zero exit
        from the real hook can never fall through to the ``||`` arm and spool a
        duplicate.

        ``daemon_url`` rides as an ARGV rather than being read from config,
        because this process is the hottest in the system and resolving
        the cascade would put a config load on every hook event. It is also
        what makes the daemon push **structurally host-only**: the flag reaches
        the entry point, and the entry point is precisely the thing that does
        not exist where the push could not work anyway.
        """
        flag = f" {_DAEMON_URL_FLAG} {shlex.quote(daemon_url)}" if daemon_url else ""
        return (
            f"command -v {cls.COMMAND} >/dev/null 2>&1 && exec {cls.COMMAND}{flag} "
            f"|| {cls.spool_script(spool_dir)}"
        )

    @staticmethod
    def push(payload: dict[str, Any], *, daemon_url: str) -> None:
        """POST one hook event to the daemon's ingest route. Never raises.

        The live half of the push: the sidecar is the offline truth, this only
        collapses the ~2s poll-tick lag into an immediate recompute.

        **It runs HERE, in the entry point, rather than as a second ``http``
        handler in the settings file, and that placement is the fix for two
        things at once.** A container has no route to the daemon's
        loopback, so a registered http handler failed on EVERY event — Claude
        Code reports that in its own UI as ``<Event> hook error: connect
        ECONNREFUSED 127.0.0.1:7421``, once per event, which is the same
        user-facing damage the missing binary caused and is not fixed by
        fixing the binary. A host with no daemon running showed the identical
        banner for the identical reason. Moving the call into the one process
        that only exists on the host makes both disappear structurally: no
        reachable daemon, no push, no error, and no runtime branch anywhere
        deciding that. The second gain is that the settings file — which is
        bind-mounted into every container — no longer carries a live daemon
        bearer token at all.

        The trade weighed against this was serializing the POST behind the
        sidecar write instead of letting Claude dispatch both handlers in
        parallel. It is a loopback call the agent's turn already waited on
        (Claude awaits every handler), so what is actually added is the
        sidecar write, measured in milliseconds.

        ``urllib`` rather than ``httpx``: the import cost of an HTTP client
        would land on every hook event, which is exactly the tax giving this
        its own entry point removed.
        """
        request = urllib.request.Request(
            f"{daemon_url}{HOOK_INGEST_ROUTE}",
            data=json.dumps({"session_id": payload.get("session_id", "")}).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {ClaudeHook.ensure_ingest_token()}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=PUSH_TIMEOUT_SECONDS):
                pass
        except (OSError, ValueError) as exc:
            # A daemon that is down, slow, or not listening is the ordinary
            # case, not an error: the sidecar this process just wrote is the
            # signal, and the next poll picks it up.
            logger.debug("could not push hook event to {}: {}", daemon_url, exc)

    @classmethod
    def drain(cls, *, sidecar_dir: Path, spool_dir: Path | None = None) -> int:
        """Fold every spooled payload into a sidecar; return how many were folded.

        The host half of :meth:`spool_script`, and it runs through
        :meth:`record_event` — the SAME fold a host hook performs — so a
        containerized session's status, its ask-time question capture and its
        clear rules are the ones this module already defines, not a second
        approximation of them. `record_event` itself routes a spooled payload
        to the sub-agent sidecar when it carries an `agent_id`, exactly as a
        direct host write would — the drain has no separate sub-agent branch.

        **Ordering and time both come from the spool file's mtime.** The
        pending-question machine is a state machine over the prior sidecar, so
        folding out of order would leave a question standing that a later event
        cleared; and stamping the record with the *drain's* clock would age
        every event by however long the reader took to notice it, which
        `HookRecord.supersedes_poll` reads as staleness. The file's mtime is the
        moment the agent actually fired.

        Claim-by-rename because a daemon drains from its poll thread and its
        request executors at once: ``rename`` is atomic, so exactly one drainer
        owns each payload and a loser simply moves on. Best-effort throughout —
        an unreadable or malformed entry is dropped rather than blocking the
        queue behind it, and no failure here may break a read.
        """
        spool = paths.agent_hook_spool_dir(sidecar_dir) if spool_dir is None else spool_dir
        try:
            entries = sorted(
                ((path.stat().st_mtime, path) for path in spool.glob(f"*{cls.SPOOL_SUFFIX}")),
                key=lambda item: (item[0], item[1].name),
            )
        except OSError:
            return 0
        folded = 0
        for mtime, path in entries:
            claimed = path.with_name(f"{path.name}.{uuid.uuid4().hex}.claimed")
            try:
                path.rename(claimed)
            except OSError:
                continue  # another drainer got there first
            try:
                payload = json.loads(claimed.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    cls.record_event(
                        payload,
                        sidecar_dir=sidecar_dir,
                        # A spooled payload carries no `$TMUX_PANE`: the pane it
                        # would name is the CONTAINER's own tmux, which no host
                        # reader can resolve, and `live_here_at` treats a `None`
                        # pane as "no live-here evidence" — so adoption falls
                        # back to transcript birth rather than matching against
                        # a pane from a foreign namespace.
                        tmux_pane=None,
                        now=datetime.fromtimestamp(mtime, tz=UTC),
                    )
                    folded += 1
            except (OSError, ValueError) as exc:
                logger.debug("could not fold spooled hook payload {}: {}", path, exc)
            finally:
                try:
                    claimed.unlink(missing_ok=True)
                except OSError as exc:
                    logger.debug("could not remove spooled hook payload {}: {}", claimed, exc)
        return folded

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
        """Turn one hook payload into a sidecar write. ``None`` if the event is
        ignored OR the payload is sub-agent-scoped (see below).

        Keyed by ``(session_id, agent_id)``: every event carries `agent_id`
        whenever it fired from inside a sub-agent, and its presence is the
        WHOLE dispatch — routed to :meth:`_record_subagent_event`, which
        writes that agent's OWN sidecar and never touches this session's
        main-thread one. Before this split, a sub-agent's `PreToolUse` was
        keyed identically to the main thread's and silently overwrote the
        top-level session's pushed status while the sub-agent ran. A
        main-thread event (no `agent_id`) is unaffected — same shape as
        before.

        Best-effort: a malformed payload or unwritable dir is logged and
        swallowed — a hook must never break the agent it instruments.
        """
        event = str(payload.get("hook_event_name", ""))
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None
        agent_id = _opt_str(payload.get("agent_id"))
        if agent_id is not None:
            cls._record_subagent_event(
                payload,
                event,
                session_id=session_id,
                agent_id=agent_id,
                sidecar_dir=sidecar_dir,
                now=now,
            )
            return None
        state = cls.state_for(event, payload)
        if state is None:
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
    def _record_subagent_event(
        cls,
        payload: dict[str, Any],
        event: str,
        *,
        session_id: str,
        agent_id: str,
        sidecar_dir: Path,
        now: datetime,
    ) -> SubagentHookRecord | None:
        """The ``(session_id, agent_id)``-keyed half of :meth:`record_event`.

        Mirrors the main-thread shape (state map → sidecar write) but against
        :data:`_SUBAGENT_STATE_BY_EVENT` and this agent's OWN sidecar file, so
        a sub-agent's tool calls can never corrupt the top-level session's
        pushed status. ``started_at`` and ``current_tool`` are read-modify-write
        against the PRIOR record for this same agent — `started_at` is stamped
        once (at `SubagentStart`, or at whatever event this agent's sidecar
        first sees, degrading gracefully if that event was ever missed) and
        held; `current_tool` is cleared on `PostToolUse` and carried forward on
        every event that isn't itself a tool boundary. ``None`` for an event
        this map doesn't recognize (defensive — a main-thread-only event
        should never carry an `agent_id`, but a future Claude Code release is
        not this module's to predict).
        """
        state = _SUBAGENT_STATE_BY_EVENT.get(event)
        if state is None:
            return None
        prior = cls._read_subagent(session_id, agent_id, sidecar_dir=sidecar_dir)
        started_at = prior.started_at if prior is not None else now
        if event == "PreToolUse":
            current_tool = _opt_str(payload.get("tool_name"))
        elif event == "PostToolUse":
            current_tool = None
        else:
            current_tool = prior.current_tool if prior is not None else None
        last_message = prior.last_message if prior is not None else None
        if event == "SubagentStop":
            pushed = _opt_str(payload.get("last_assistant_message"))
            if pushed is not None:
                last_message = _truncate_message(pushed)
        record = SubagentHookRecord(
            session_id=session_id,
            agent_id=agent_id,
            agent_type=_opt_str(payload.get("agent_type")),
            state=state,
            event=event,
            started_at=started_at,
            last_event_at=now,
            current_tool=current_tool,
            last_message=last_message,
        )
        cls._write_subagent(record, sidecar_dir=sidecar_dir)
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
        """The pending question this event leaves standing.

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
        # `_read`, never `read`: this runs INSIDE a fold, and the public read
        # drains the spool first — which would re-enter the fold it is part of.
        prior = cls._read(session_id, sidecar_dir=sidecar_dir)
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
    def _subagent_sidecar_path(session_id: str, agent_id: str, *, sidecar_dir: Path) -> Path:
        """Where one sub-agent's own sidecar lives — a directory PER top-level
        session, one file per agent, so :meth:`list_subagents` is a single
        directory listing with no index to build or invalidate."""
        return sidecar_dir / _SUBAGENT_SIDECAR_DIR / session_id / f"{agent_id}.json"

    @classmethod
    def _write_subagent(cls, record: SubagentHookRecord, *, sidecar_dir: Path) -> None:
        """Atomically write one sub-agent's sidecar. Best-effort (never raises)."""
        try:
            target = cls._subagent_sidecar_path(
                record.session_id, record.agent_id, sidecar_dir=sidecar_dir
            )
            paths.ensure_dir(target.parent)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(record.to_json()), encoding="utf-8")
            os.replace(tmp, target)
        except OSError as exc:
            logger.debug(
                "could not write sub-agent sidecar for {}/{}: {}",
                record.session_id,
                record.agent_id,
                exc,
            )

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
        (`ActivityService.sessions_for`, `SessionExplorer.for_workspace`):

        - transcript BIRTH (``born_at``, immutable) — the pure predicate's job;
        - a hook sidecar proving the session was live *in this workspace* — the
          boundary's job, distilled here from the pre-read ``candidate`` sidecar
          via :meth:`live_here_at`.

        Takes the already-read ``candidate`` record and the workspace's
        ``reference_pane`` (both resolved once per session per tick by the
        caller) so no sidecar is re-read. ``reference_pane`` is the
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
        pane: its transcript is born before the workspace, so birth can't
        adopt it (`WorkspaceState.adopts_session`), but a hook sidecar recorded
        from the workspace's own pane *after* creation can.

        Attribution is **pane-verified**: the candidate sidecar's
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
        discipline.
        """
        if record is None or record.cwd is None or reference_pane is None:
            return None
        if record.tmux_pane != reference_pane:
            return None
        return record.ts if Path(record.cwd).resolve() == cwd.resolve() else None

    @classmethod
    def read(cls, session_id: str, *, sidecar_dir: Path) -> HookRecord | None:
        """Read a session's sidecar, or ``None`` if missing or malformed.

        Pure mechanism — whether the push still outranks the polled blend is the
        record's own call (:meth:`HookRecord.supersedes_poll`), because that
        judgment needs the transcript's clock, which only the blend site has.

        **Folding the spool happens HERE, at the one seam every consumer already
        calls.** A containerized hook can only drop raw payloads
        (:meth:`spool_script`), so something has to turn them into sidecars, and
        the four independent readers — the activity blend, its question
        cross-check, session adoption and `answer_question` — are exactly the
        shape this tree has watched a two-line convention get missed at, one
        site at a time, until a pinned workspace lit up only partially. Owning
        it in `read` means a reader has nothing left to get wrong, and a new one
        inherits it. Cost on the host, where nothing ever spools: one
        directory-listing syscall.
        """
        cls.drain(sidecar_dir=sidecar_dir)
        return cls._read(session_id, sidecar_dir=sidecar_dir)

    @staticmethod
    def _read(session_id: str, *, sidecar_dir: Path) -> HookRecord | None:
        """The bare sidecar read, without draining. See :meth:`read`."""
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

    @classmethod
    def read_subagent(
        cls, session_id: str, agent_id: str, *, sidecar_dir: Path
    ) -> SubagentHookRecord | None:
        """One sub-agent's pushed status, or ``None`` if missing/malformed.

        Drains the spool first, exactly like :meth:`read` — a containerized
        sub-agent's hook can only spool its raw payload (see
        :meth:`spool_script`), and this is one of the seams that must fold it
        before answering.
        """
        cls.drain(sidecar_dir=sidecar_dir)
        return cls._read_subagent(session_id, agent_id, sidecar_dir=sidecar_dir)

    @classmethod
    def _read_subagent(
        cls, session_id: str, agent_id: str, *, sidecar_dir: Path
    ) -> SubagentHookRecord | None:
        """The bare per-agent sidecar read, without draining. See :meth:`read_subagent`."""
        path = cls._subagent_sidecar_path(session_id, agent_id, sidecar_dir=sidecar_dir)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("could not read sub-agent sidecar {}: {}", path, exc)
            return None
        return SubagentHookRecord.from_json(data)

    @classmethod
    def list_subagents(
        cls, session_id: str, *, sidecar_dir: Path
    ) -> tuple[SubagentHookRecord, ...]:
        """Every sub-agent this session has pushed status for, oldest-started first.

        A directory listing plus one small read per entry — no index, no
        transcript parse, no discovery scan. Drains the spool ONCE for the
        whole session (not once per entry), so a containerized fleet's whole
        backlog folds in one pass. Best-effort: a malformed entry is skipped,
        never raised; a session with no sub-agents (the overwhelming common
        case) costs one missing-directory stat.
        """
        cls.drain(sidecar_dir=sidecar_dir)
        directory = sidecar_dir / _SUBAGENT_SIDECAR_DIR / session_id
        try:
            found = sorted(directory.glob("*.json"))
        except OSError:
            return ()
        out: list[SubagentHookRecord] = []
        for path in found:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.debug("could not read sub-agent sidecar {}: {}", path, exc)
                continue
            record = SubagentHookRecord.from_json(data)
            if record is not None:
                out.append(record)
        out.sort(key=lambda r: r.started_at)
        return tuple(out)

    @staticmethod
    def ensure_ingest_token() -> str:
        """Get-or-create the same-host secret the http hook and the daemon's
        ingest route both trust.

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

    @classmethod
    def settings(
        cls,
        command: str | None = None,
        *,
        daemon_url: str | None = DEFAULT_DAEMON_LOOPBACK_URL,
    ) -> dict[str, Any]:
        """The Claude Code settings dict that installs the Grove hook on every event.

        Hook-only: Grove writes this to its own file and passes it via
        ``claude --settings``, so the user's ``.claude/settings.json`` is never
        touched.

        ``command`` defaults to :meth:`hook_command` — the entry point where it
        exists, the spool fallback where it does not — so ONE rendered file
        installs working hooks on a host and inside a container alike.
        Pass an explicit string to pin it (the test seam).

        **ONE handler per event.** The daemon push is made from the
        entry point (:meth:`push`), with ``daemon_url`` reaching it as
        an argv on the rendered command — never as a second, registered ``http``
        handler, because a containerized session has no route to the daemon's
        loopback from another network namespace, and a handler registered in a
        file cannot ask whether the address it names is reachable. See
        :meth:`push` for the full trade. ``daemon_url=None`` renders a command
        that pushes nothing (the test seam, and the shape an operator gets by
        pointing `hooks.daemon_url` nowhere).

        Registers a WIDER event set than the state map: `_SUBAGENT_EVENTS`
        never move the sidecar's state (`state_for` returns ``None`` for
        both — a sub-agent lifecycle never flips the main thread) but still
        deserve a push refresh, since a finishing sub-agent changes the
        transcript. ``Notification`` is split into its `_NOTIFICATION_MATCHERS`
        sub-kinds as separate matcher entries rather than one catch-all —
        `state_for` still keys off the bare event name (all three collapse to
        BLOCKED); the split is registration-only.
        """
        resolved = (
            cls.hook_command(paths.agent_hook_spool_dir(), daemon_url=daemon_url)
            if command is None
            else command
        )
        handlers: list[dict[str, Any]] = [{"type": "command", "command": resolved}]
        catch_all = [{"hooks": handlers}]
        hooks: dict[str, Any] = dict.fromkeys((*_STATE_BY_EVENT, *_SUBAGENT_EVENTS), catch_all)
        hooks["Notification"] = [
            {"matcher": matcher, "hooks": handlers} for matcher in _NOTIFICATION_MATCHERS
        ]
        return {"hooks": hooks}


def run_hook_from_stdin(argv: Sequence[str] | None = None) -> int:
    """CLI edge for ``grove-agent-hook``: one payload → the sidecar, then the push.

    Always returns 0 — a hook must never fail the agent it instruments, so a
    malformed payload or an ignored event is a silent no-op. ``$TMUX_PANE`` is
    read here (the edge) and threaded into the record so a future pane-targeting
    consumer can map a session to its window without a second tmux call.

    The optional ``--daemon-url <url>`` (rendered onto the command by
    :meth:`ClaudeHook.hook_command`) turns on the immediate daemon refresh.
    Deliberately hand-parsed rather than given an arg parser: this is the
    hottest process in the system and the whole reason it is a dedicated entry
    point instead of a ``grove`` subcommand was to import nothing it does not
    need.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    daemon_url = ""
    if _DAEMON_URL_FLAG in args:
        index = args.index(_DAEMON_URL_FLAG) + 1
        daemon_url = args[index] if index < len(args) else ""
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
    # After the sidecar, never before: the file is the offline truth and the
    # push only asks the daemon to look at it sooner.
    if daemon_url:
        ClaudeHook.push(payload, daemon_url=daemon_url)
    _emit_brief(payload)
    return 0


def _emit_brief(payload: dict[str, Any]) -> None:
    """Print the first-turn brief, at most once per session. See `brief.py`.

    ``UserPromptSubmit`` is the ONE event whose stdout Claude Code injects into
    the model's context, which is why this is the only event that emits
    anything — and why the emit sits at the very end, after the two side effects
    that must not be able to put a byte on stdout ahead of it.

    Everything here is best-effort in the strongest sense: no env var, no
    readable brief, or a session already briefed all print nothing, and the
    caller still returns 0. **Exit 2 BLOCKS the user's prompt outright**, so a
    raise from a decorative feature would cost the user their turn.

    Deliberately no config load and no cwd→workspace resolution — this process
    runs on every hook event of every session in the fleet, so the whole
    decision is one env read plus at most two file operations.
    """
    if payload.get("hook_event_name") != "UserPromptSubmit":
        return
    brief_path = os.environ.get(AgentBrief.PATH_ENV)
    session_id = payload.get("session_id")
    if not brief_path or not isinstance(session_id, str):
        return
    text = AgentBrief.consume(
        session_id, brief_path=Path(brief_path), sidecar_dir=paths.agent_sidecar_dir()
    )
    if not text:
        return
    try:
        print(text)
    except OSError as exc:
        # A closed or broken stdout is the reader's business, never this
        # process's: the brief is already marked delivered, and raising here
        # would exit non-zero and block the prompt.
        logger.debug("could not emit the first-turn brief: {}", exc)


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _truncate_message(text: str, cap: int = _LAST_MESSAGE_CAP) -> str:
    """Collapse whitespace and cap length, trailing ellipsis as the trim
    signal — the same shape `claude_code.py`'s own `_truncate` uses, kept as a
    small local copy rather than an import: `hook.py` is the hottest process
    in the system and stays free of that module's dependency weight."""
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"
