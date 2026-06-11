"""Normalized, tool-agnostic agent-session model.

These shapes are the seam between a specific agent tool (Claude Code today,
opencode / codex tomorrow) and the rest of Grove. An :class:`AgentAdapter`
turns that tool's on-disk transcript into these dataclasses; the
``ActivityService`` and both clients consume *only* these, never the tool's
native JSONL. That is what makes the dashboard extensible without touching
clients (epic #11 §3).

Plain frozen dataclasses, not Pydantic: this is internal in-process IR that
never crosses a wire by itself — the ``contracts/`` Views do the serializing.
See CLAUDE.md, "Pydantic at public-contract boundaries; plain dataclass for
in-process state".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

# How Grove came to know about a session. ``grove_launched`` is the deterministic
# path (Grove minted the ``--session-id``); the other two are out-of-band adoption
# layered in by #18. Kept as a Literal — a closed set that drives branching.
SessionProvenance = Literal["grove_launched", "hook_discovered", "fs_discovered"]


class AgentActivityState(StrEnum):
    """What one agent session is doing right now (epic #11 §6).

    Computed live from the transcript blended with tmux activity; never
    persisted. The transcript-only adapter emits a subset (WORKING / WAITING /
    ERROR / UNKNOWN); the ``ActivityService`` is the single policy site that
    layers in STARTING (session id known, no file yet), IDLE (alive but tmux
    quiet), and — once #18 lands — BLOCKED (permission prompt from a hook).
    """

    STARTING = "starting"  # session id known, transcript not yet on disk
    WORKING = "working"  # in the tool loop or mid-response
    WAITING = "waiting"  # turn ended; may need the human
    BLOCKED = "blocked"  # explicit permission / input prompt (hook-sourced)
    IDLE = "idle"  # alive but no recent activity
    ERROR = "error"  # parse/process error or a failed run
    UNKNOWN = "unknown"  # transcript unreadable / suppressed


# States that mean "the agent wants the human". Drives ``needs_attention`` and the
# dashboard's "what needs me" lens. A frozenset so membership is the whole test.
ATTENTION_STATES: frozenset[AgentActivityState] = frozenset(
    {AgentActivityState.WAITING, AgentActivityState.BLOCKED, AgentActivityState.ERROR}
)


@dataclass(slots=True, frozen=True)
class AgentSession:
    """One tracked agent run inside a workspace — identity, not activity.

    ``transcript_path`` is ``None`` until the tool writes the file (Claude Code
    creates it lazily on the first turn), which is exactly the STARTING window.
    """

    session_id: str
    transcript_path: Path | None
    adapter_kind: str
    provenance: SessionProvenance
    tmux_window: str | None = None


@dataclass(slots=True, frozen=True)
class DigestEntry:
    """One line of an :class:`OrderedDigest`: a role tag plus a short summary."""

    role: Literal["user", "assistant", "tool", "summary", "status"]
    text: str


@dataclass(slots=True, frozen=True)
class OrderedDigest:
    """Compact, ordered slice of a transcript for the future LLM interpreter (#20).

    The ``USER → ASSISTANT → TOOL(name) → summary`` skeleton with bulky
    ``tool_result`` payloads stripped — small enough to feed an external model
    cheaply. A designed seam only: nothing in the MVP calls an LLM with it.
    """

    entries: tuple[DigestEntry, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.entries


@dataclass(slots=True, frozen=True)
class SessionTurn:
    """One conversation turn: a human prompt plus everything until the next one.

    ``entries`` carries the assistant replies and tool calls inside the turn as
    :class:`DigestEntry` rows (full text, not the digest's truncated form).
    ``user_text`` is empty for a leading continuation block — assistant records
    that precede any human turn in the file (a resumed/compacted session).
    """

    user_text: str
    started_at: datetime | None = None
    entries: tuple[DigestEntry, ...] = ()


@dataclass(slots=True, frozen=True)
class AgentActivity:
    """Live, computed-from-transcript activity for one agent session.

    Never persisted; recomputed on demand and best-effort (a corrupt transcript
    degrades fields, it never raises). ``replies_per_turn`` is the per-turn
    breakdown the original request asked for ("replies between each user turn"):
    ``human_turns == len(replies_per_turn)`` and
    ``assistant_replies == sum(replies_per_turn)`` hold by construction.

    ``needs_attention`` is a *derived* property, not a stored field, so its rule
    lives in exactly one place. The per-client "already viewed?" mask (Crystal's
    ``lastViewedAt < updatedAt``) is applied by the client, not here.
    """

    state: AgentActivityState
    title: str | None = None
    current_task: str | None = None
    human_turns: int = 0
    assistant_replies: int = 0
    replies_per_turn: tuple[int, ...] = ()
    tool_calls: int = 0
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    last_event_at: datetime | None = None
    error_detail: str | None = None
    # Reserved seam for the future external-LLM task interpreter (#20). The
    # adapter's `transcript_digest()` produces the compact, tool_result-stripped
    # slice an external model would read; a user-configured `InterpreterService`
    # would populate this with a one-line human summary. Off by default and not
    # wired in the MVP — the field reserves the dashboard space so adding the
    # interpreter later needs no contract change (YAGNI: seam now, call later).
    interpreted_status: str | None = None

    @property
    def needs_attention(self) -> bool:
        """True when the state is one that wants the human (epic §6)."""
        return self.state in ATTENTION_STATES

    @classmethod
    def empty(cls, state: AgentActivityState = AgentActivityState.UNKNOWN) -> AgentActivity:
        """An activity with no metrics — for an unreadable or not-yet-written transcript."""
        return cls(state=state)


@dataclass(slots=True, frozen=True)
class SessionSummary:
    """Identity + listing metadata for one on-disk session (a `sessions list` row).

    Field names deliberately mirror the official Agent SDK's ``SDKSessionInfo``
    (``first_prompt`` / ``git_branch`` / ``cwd`` / ``created_at``) so Grove's
    normalized model stays recognizable next to the documented contract.
    ``activity`` is the same point-in-time parse the dashboard computes — one
    pass over the file yields both the metadata and the metrics, so listing
    never reads a transcript twice.
    """

    session_id: str
    adapter_kind: str
    transcript_path: Path
    cwd: str | None
    created_at: datetime | None
    modified_at: datetime | None
    size_bytes: int
    git_branch: str | None = None
    title: str | None = None
    first_prompt: str | None = None
    last_prompt: str | None = None
    activity: AgentActivity = field(default_factory=AgentActivity.empty)
