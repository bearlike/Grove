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

from dataclasses import dataclass, field, replace
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


# A structured question an agent asked the user (epic #74). One closed set of
# kinds drives the client's rendering affordance; it is provider-neutral — the
# adapters map a native tool call onto it, never the reverse.
AgentQuestionKind = Literal["single_select", "multi_select", "free_text", "confirm"]

# The native tool names that *are* a question. Single source of truth: the
# normalizer below recognizes exactly these, and the Claude status path reuses
# the same set to flag an unanswered tail as BLOCKED. Adding a provider's
# question tool is one entry here.
QUESTION_TOOL_NAMES: frozenset[str] = frozenset({"AskUserQuestion", "ExitPlanMode"})


@dataclass(slots=True, frozen=True)
class AgentQuestionOption:
    """One selectable choice in an :class:`AgentQuestion` — a label, optionally
    a one-line description. Shape only; the adapter never invents semantics."""

    label: str
    description: str | None = None


@dataclass(slots=True, frozen=True)
class AgentQuestion:
    """A provider-neutral question an agent asked the user (epic #74).

    The normalized target every adapter maps its native ask-the-human tool onto
    (Claude Code's ``AskUserQuestion`` / ``ExitPlanMode``; a Codex MCP-bridged
    equivalent). ``id`` is the stable per-question answer-back address and
    ``group_id`` the native tool-call id a *batch* shares — the two together are
    what a future "answer back" write-path (and the #70 notifier) address, so
    they are part of the contract even though the MVP only renders. ``answered``
    /``answer`` model resolution (a matching ``tool_result`` / ``function_call_
    output``) at the group level — no semantic per-question split of the result.
    """

    id: str
    group_id: str
    kind: AgentQuestionKind
    prompt: str
    header: str | None = None
    options: tuple[AgentQuestionOption, ...] = ()
    multiselect: bool = False
    answered: bool = False
    answer: str | None = None
    source_tool: str = ""

    @staticmethod
    def recognizes(tool_name: str) -> bool:
        """Whether a native tool name is an ask-the-human question tool.

        The one predicate the status path shares with normalization, so "what is
        a question" is defined exactly once.
        """
        return tool_name in QUESTION_TOOL_NAMES

    @classmethod
    def from_tool_call(
        cls, tool_name: str, raw_input: object, call_id: str
    ) -> tuple[AgentQuestion, ...]:
        """Normalize one native tool call into zero or more questions.

        The single normalization seam both adapters call (DRY across the provider
        boundary). Returns ``()`` for any non-question tool or a malformed payload
        — defensive like the rest of transcript parsing: a junk block is dropped,
        never raised, so it can't break the render loop. A batch
        (``AskUserQuestion`` with N questions) yields N rows whose ``id`` encodes
        the source position (``f"{call_id}#{i}"``), stable across re-parses.
        """
        if tool_name == "ExitPlanMode":
            plan = raw_input.get("plan") if isinstance(raw_input, dict) else None
            prompt = plan if isinstance(plan, str) and plan.strip() else "Approve this plan?"
            return (
                cls(
                    id=f"{call_id}#0",
                    group_id=call_id,
                    kind="confirm",
                    prompt=prompt,
                    source_tool="ExitPlanMode",
                ),
            )
        if tool_name != "AskUserQuestion" or not isinstance(raw_input, dict):
            return ()
        questions = raw_input.get("questions")
        if not isinstance(questions, list):
            return ()
        out: list[AgentQuestion] = []
        for i, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            text = q.get("question")
            if not isinstance(text, str) or not text.strip():
                continue
            options = cls._options(q.get("options"))
            # Shape, not semantics: a question with no choices is a descriptive
            # / free-text ask (an MCP-bridged elicitation), not a select with an
            # empty list — and ``multiSelect`` is moot when there is nothing to
            # select. With choices, ``multiSelect`` picks single vs multi.
            multiselect = bool(options) and bool(q.get("multiSelect"))
            if not options:
                kind: AgentQuestionKind = "free_text"
            elif multiselect:
                kind = "multi_select"
            else:
                kind = "single_select"
            out.append(
                cls(
                    id=f"{call_id}#{i}",
                    group_id=call_id,
                    kind=kind,
                    prompt=text,
                    header=q.get("header") if isinstance(q.get("header"), str) else None,
                    options=options,
                    multiselect=multiselect,
                    source_tool="AskUserQuestion",
                )
            )
        return tuple(out)

    @staticmethod
    def _options(raw: object) -> tuple[AgentQuestionOption, ...]:
        if not isinstance(raw, list):
            return ()
        out: list[AgentQuestionOption] = []
        for opt in raw:
            if not isinstance(opt, dict):
                continue
            label = opt.get("label")
            if not isinstance(label, str) or not label:
                continue
            desc = opt.get("description")
            out.append(
                AgentQuestionOption(
                    label=label, description=desc if isinstance(desc, str) else None
                )
            )
        return tuple(out)

    def resolved(self, answer: str | None) -> AgentQuestion:
        """A copy stamped answered with the group-level result text.

        Called by the parser once a matching result record is found — keeps the
        frozen contract immutable and the resolution policy in one place.
        """
        return replace(self, answered=True, answer=answer)


@dataclass(slots=True, frozen=True)
class DigestEntry:
    """One line of an :class:`OrderedDigest`: a role tag plus a short summary.

    ``question`` is populated *only* for ``role=="question"`` — the structured
    payload the transcript renderers (TUI + webapp) draw as a choice card; for
    every other role it is ``None`` and ``text`` carries the line. A question
    entry keeps ``text`` set to its prompt so a role-unaware consumer (the
    ``OrderedDigest`` LLM-interpreter seam) still reads something sensible.
    """

    role: Literal["user", "assistant", "tool", "summary", "status", "notification", "question"]
    text: str
    question: AgentQuestion | None = None


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
    # Sub-agent / background-task fleet still in flight: spawned (an Agent/Task
    # tool_use) but not yet returned (no tool_result and no task-notification
    # closing its tool-use id). The dashboard's "what runs in the background" count.
    active_subagents: int = 0
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
    never reads a transcript twice. ``transcript_path`` is ``None`` for a
    remote-backed session (no local file) — and it stays off the wire either
    way (``contracts/sessions.py``).
    """

    session_id: str
    adapter_kind: str
    transcript_path: Path | None
    cwd: str | None
    created_at: datetime | None
    modified_at: datetime | None
    size_bytes: int
    git_branch: str | None = None
    title: str | None = None
    first_prompt: str | None = None
    last_prompt: str | None = None
    activity: AgentActivity = field(default_factory=AgentActivity.empty)
