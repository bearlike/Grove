"""Normalized, tool-agnostic agent-session model.

These shapes are the seam between a specific agent tool (Claude Code today,
opencode / codex tomorrow) and the rest of Grove. An :class:`AgentAdapter`
turns that tool's on-disk transcript into these dataclasses; the
``ActivityService`` and both clients consume *only* these, never the tool's
native JSONL. That is what makes the dashboard extensible without touching
clients.

Plain frozen dataclasses, not Pydantic: this is internal in-process IR that
never crosses a wire by itself — the ``contracts/`` Views do the serializing.
See CLAUDE.md, "Pydantic at public-contract boundaries; plain dataclass for
in-process state".
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

# How Grove came to know about a session. ``grove_launched`` is the deterministic
# path (Grove minted the ``--session-id``); the other two are out-of-band adoption.
# Kept as a Literal — a closed set that drives branching.
SessionProvenance = Literal["grove_launched", "hook_discovered", "fs_discovered"]


class AgentActivityState(StrEnum):
    """What one agent session is doing right now.

    Computed live from the transcript blended with tmux activity; never
    persisted. The transcript-only adapter emits a subset (WORKING / WAITING /
    ERROR / UNKNOWN); the ``ActivityService`` is the single policy site that
    layers in STARTING (session id known, no file yet), IDLE (alive but tmux
    quiet), and BLOCKED (permission prompt from a hook).
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

    ``parent_session_id`` is ``None`` for a normal top-level session (Grove-launched
    or discovered); for an itemized sub-agent fleet member it is the
    *primary* session's own ``session_id`` — the wire's parent/child link, so a
    client can group a workspace's flat ``sessions`` list back into a tree
    without a second lookup.
    """

    session_id: str
    transcript_path: Path | None
    adapter_kind: str
    provenance: SessionProvenance
    tmux_window: str | None = None
    parent_session_id: str | None = None


# A structured question an agent asked the user. One closed set of
# kinds drives the client's rendering affordance; it is provider-neutral — the
# adapters map a native tool call onto it, never the reverse.
AgentQuestionKind = Literal["single_select", "multi_select", "free_text", "confirm"]

# The native tool names that *are* a question. Single source of truth: the
# normalizer below recognizes exactly these, and both providers' status paths
# reuse the same set to flag an unanswered tail as BLOCKED. Adding a provider's
# question tool is one entry here.
#
# Claude Code asks through ``AskUserQuestion`` (a batch) / ``ExitPlanMode`` (a
# plan confirm); Codex CLI asks through its own native ``request_user_input``
# tool, whose payload is the SAME ``questions[]`` shape (verified on-host
# against real rollouts, and against the schema in the codex-cli 0.147.0 binary
# — see agents/CLAUDE.md). That is why the batch branch below keys off this set
# rather than one literal name.
QUESTION_TOOL_NAMES: frozenset[str] = frozenset(
    {"AskUserQuestion", "ExitPlanMode", "request_user_input"}
)


@dataclass(slots=True, frozen=True)
class AgentQuestionOption:
    """One selectable choice in an :class:`AgentQuestion` — a label, optionally
    a one-line description. Shape only; the adapter never invents semantics."""

    label: str
    description: str | None = None


@dataclass(slots=True, frozen=True)
class AgentQuestion:
    """A provider-neutral question an agent asked the user.

    The normalized target every adapter maps its native ask-the-human tool onto
    (Claude Code's ``AskUserQuestion`` / ``ExitPlanMode``; Codex CLI's native
    ``request_user_input``). ``id`` is the stable per-question answer-back address and
    ``group_id`` the native tool-call id a *batch* shares — the two together are
    what the "answer back" write-path (and the notifier) address, so
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
        (``AskUserQuestion`` / ``request_user_input`` with N questions) yields N
        rows whose ``id`` encodes the source position (``f"{call_id}#{i}"``),
        stable across re-parses.

        Claude's ``AskUserQuestion`` and Codex's ``request_user_input`` carry the
        SAME batch payload — ``questions[]`` of ``question`` / ``header`` /
        ``options[{label, description}]`` — so one branch normalizes both.
        Codex's per-question ``id`` (its answer-map key) is deliberately NOT
        adopted as :attr:`id`: resolution here is group-level by contract, and a
        positional id keeps one answer-back address shape across providers.
        Codex emits no ``multiSelect`` key, so its questions normalize to
        ``single_select`` — which is the truth, since the CLI itself rejects a
        ``request_user_input`` question with no options and offers the free-form
        answer as a client-side extra choice rather than a schema variant.
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
        if not cls.recognizes(tool_name) or not isinstance(raw_input, dict):
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
                    source_tool=tool_name,
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
class AnswerSelection:
    """One question's validated answer, in-process IR (never crosses a wire).

    The provider-neutral input a keystroke builder consumes: ``indexes`` picks
    predefined options (0-based, in option order), ``text`` is a free-text
    answer. Exactly one is meaningful per question — the wire model
    (``contracts.questions``) enforces the XOR before this is built; this dataclass
    just carries the validated choice from the manager to the adapter. Kept next
    to :class:`AgentQuestion` because the two are the builder's paired inputs.
    """

    indexes: tuple[int, ...] = ()
    text: str | None = None


# The native tool names that mutate a file's contents. Single source of truth:
# the normalizer below recognizes exactly these. Each is a name a provider Grove
# parses actually emits — Claude Code's Edit/MultiEdit/Write, Codex's apply_patch,
# and str_replace (an MCP-bridged edit tool, also what Mewbo surfaces as an
# operation). A name that turns out to carry an unexpected shape just falls
# through to () and the generic tool digest, never raises. Add a name only when
# a real provider emits it (no speculative aliases — YAGNI).
FILE_EDIT_TOOL_NAMES: frozenset[str] = frozenset(
    {"Edit", "MultiEdit", "Write", "str_replace", "apply_patch"}
)


@dataclass(slots=True, frozen=True)
class FileEdit:
    """One file mutation a tool call performed, provider-normalized (Claude
    Code ``Edit``/``MultiEdit``/``Write``, Codex ``apply_patch``, Mewbo's
    equivalent) so every renderer draws one diff card instead of a bare tool
    name — the file-edit sibling of :class:`AgentQuestion`.

    ``old_text`` is empty for a from-scratch write, or whenever the tool's own
    arguments simply never carried a "before" (a full-file ``Write`` over an
    already-existing file) — it is **never backfilled from disk**. A post-hoc
    read can't recover pre-edit content anyway: by the time any parser runs
    (let alone a fetch-on-demand ``/turns`` request, possibly long after), the
    file already reflects the edit. An honest all-additions diff beats a
    fabricated or stale "before".
    """

    path: str
    old_text: str
    new_text: str

    _PATCH_PATH_MARKERS = ("*** Update File: ", "*** Add File: ", "+++ b/", "--- a/")
    _PATCH_META_PREFIXES = ("@@", "diff ", "index ", "*** ", "---", "+++")

    @staticmethod
    def recognizes(tool_name: str) -> bool:
        """Whether a native tool name is a file-edit tool — the one predicate
        both the normalizer and any future status/counting logic should share,
        mirroring :meth:`AgentQuestion.recognizes`."""
        return tool_name in FILE_EDIT_TOOL_NAMES

    @classmethod
    def from_tool_call(cls, tool_name: str, raw_input: object) -> tuple[FileEdit, ...]:
        """Normalize one native tool call into zero or more file edits.

        Defensive like :meth:`AgentQuestion.from_tool_call`: an unrecognized
        name or a malformed/unexpected payload returns ``()`` rather than
        raising, so a provider quirk degrades to the generic tool digest
        instead of breaking the render loop. Three shapes, tried in order:

        1. A batch (``MultiEdit``'s ``edits: [...]``) — one call, N sequential
           edits on the same file, yielding N entries (same reason
           ``AgentQuestion.from_tool_call`` returns a tuple for a batch).
        2. Direct old/new-string-shaped fields on the call itself (``Edit``,
           ``str_replace``, a single ``MultiEdit``-less edit, ``Write``).
        3. A unified-diff-shaped patch body under a ``patch``/``input``/
           ``diff`` key (Codex ``apply_patch``) — reconstructed via
           :meth:`_from_patch_text`. This key name is unverified against a
           real on-host Codex rollout (see the adapter's own module docs);
           tried defensively, never assumed.
        """
        if not cls.recognizes(tool_name) or not isinstance(raw_input, dict):
            return ()
        path = cls._first_str(raw_input, "file_path", "path") or ""
        edits = raw_input.get("edits")
        if isinstance(edits, list):
            return tuple(
                edit
                for e in edits
                if isinstance(e, dict) and (edit := cls._from_fields(path, e)) is not None
            )
        edit = cls._from_fields(path, raw_input)
        if edit is not None:
            return (edit,)
        patch_text = cls._first_str(raw_input, "patch", "input", "diff")
        if patch_text:
            parsed = cls._from_patch_text(patch_text, path or None)
            if parsed is not None:
                return (parsed,)
        return ()

    @classmethod
    def _from_fields(cls, path: str, fields: dict[str, Any]) -> FileEdit | None:
        old_text = cls._first_str(fields, "old_string", "oldString", "old")
        new_text = cls._first_str(fields, "new_string", "newString", "new", "content", "file_text")
        if old_text is None and new_text is None:
            return None
        return cls(path=path, old_text=old_text or "", new_text=new_text or "")

    @classmethod
    def _from_patch_text(cls, text: str, path: str | None) -> FileEdit | None:
        """Reconstruct old/new file bodies from a unified-diff-shaped string:
        context lines feed both sides, ``-`` lines feed old only, ``+`` lines
        feed new only. Meta/header lines (``@@ ...``, ``diff --git``, Codex's
        ``*** Update File: ...``) are skipped, and a leading marker doubles as
        the path when the call carried none. Requires at least two +/- lines
        so ordinary text isn't mistaken for a diff — the same guard a
        comparable tool's text-diff detector uses.
        """
        old_lines: list[str] = []
        new_lines: list[str] = []
        signal = 0
        detected_path = path
        for line in text.splitlines():
            if line.startswith(cls._PATCH_META_PREFIXES):
                detected_path = detected_path or cls._path_from_marker(line)
                continue
            if line.startswith("+"):
                new_lines.append(line[1:])
                signal += 1
            elif line.startswith("-"):
                old_lines.append(line[1:])
                signal += 1
            else:
                body = line[1:] if line.startswith(" ") else line
                old_lines.append(body)
                new_lines.append(body)
        if signal < 2:
            return None
        return cls(
            path=detected_path or "",
            old_text="\n".join(old_lines),
            new_text="\n".join(new_lines),
        )

    @classmethod
    def _path_from_marker(cls, line: str) -> str | None:
        for prefix in cls._PATCH_PATH_MARKERS:
            if line.startswith(prefix):
                return line[len(prefix) :].strip()
        return None

    @staticmethod
    def _first_str(fields: dict[str, Any], *keys: str) -> str | None:
        """The first string value found under any of ``keys``, or ``None``."""
        for key in keys:
            value = fields.get(key)
            if isinstance(value, str):
                return value
        return None


# The native tool names that write an agent's todo/plan list. Single source of
# truth: the normalizer below recognizes exactly these. Claude Code drives a
# checklist through ``TodoWrite`` (``input.todos[]`` — ``content``/``status``,
# optionally ``activeForm``); Codex through ``update_plan`` (``plan[]`` —
# ``step``/``status``, verified on-host against real rollouts, codex 0.114). A
# name whose payload turns out unexpected falls through to () and the generic
# tool digest, never raises. Add a name only when a real provider emits it (YAGNI).
TODO_TOOL_NAMES: frozenset[str] = frozenset({"TodoWrite", "update_plan"})

# The lifecycle a todo item moves through, normalized across providers (Claude
# and Codex agree on this triple). Drives a check / spinner / dot in the render.
TodoStatus = Literal["pending", "in_progress", "completed"]
_TODO_STATUSES: frozenset[str] = frozenset({"pending", "in_progress", "completed"})


@dataclass(slots=True, frozen=True)
class TodoItem:
    """One entry in an agent's todo/plan list, provider-normalized. ``content``
    is the imperative task text (Claude ``content`` / Codex ``step``);
    ``active_form`` is Claude's optional present-tense phrasing shown while the
    item is in progress (``None`` for Codex, which carries no equivalent)."""

    content: str
    status: TodoStatus
    active_form: str | None = None


@dataclass(slots=True, frozen=True)
class TodoList:
    """An agent's current todo/plan list, provider-normalized (Claude Code
    ``TodoWrite``, Codex ``update_plan``) — the todo sibling of :class:`FileEdit`
    and :class:`AgentQuestion`. It rides the transcript as
    ``DigestEntry(role="todo", todo=…)`` so every renderer draws one checklist
    card instead of the bare ``TodoWrite``/``update_plan`` tool name, which
    would otherwise discard the whole list.

    One tool call is ONE list, so :meth:`from_tool_call` returns a 0-or-1-tuple
    (unlike the batch-returns-N contract of the file-edit/question siblings),
    which still lets an adapter share the one ``entries.extend(...)`` dispatch shape.
    """

    items: tuple[TodoItem, ...] = ()

    @staticmethod
    def recognizes(tool_name: str) -> bool:
        """Whether a native tool name writes a todo list — the one predicate the
        normalizer and any future status logic share, mirroring
        :meth:`FileEdit.recognizes` / :meth:`AgentQuestion.recognizes`."""
        return tool_name in TODO_TOOL_NAMES

    @classmethod
    def from_tool_call(cls, tool_name: str, raw_input: object) -> tuple[TodoList, ...]:
        """Normalize one todo-tool call into zero or one :class:`TodoList`.

        Defensive like :meth:`FileEdit.from_tool_call`: an unrecognized name, a
        malformed payload, or a list with no readable items returns ``()`` (the
        call then falls through to the generic tool digest) rather than raising,
        so a provider quirk can't break the render loop. The item array lives
        under ``todos`` (Claude ``TodoWrite``) or ``plan`` (Codex
        ``update_plan``); each item's task text under ``content`` (Claude) or
        ``step`` (Codex). A status that arrives unrecognized coerces to
        ``"pending"`` — shape normalization, never guessing model intent.
        """
        if not cls.recognizes(tool_name) or not isinstance(raw_input, dict):
            return ()
        raw_items = raw_input.get("todos")
        if not isinstance(raw_items, list):
            raw_items = raw_input.get("plan")
        if not isinstance(raw_items, list):
            return ()
        items = tuple(
            item
            for raw in raw_items
            if isinstance(raw, dict) and (item := cls._item(raw)) is not None
        )
        return (cls(items=items),) if items else ()

    @staticmethod
    def _item(raw: dict[str, Any]) -> TodoItem | None:
        content = raw.get("content")
        if not isinstance(content, str) or not content.strip():
            content = raw.get("step")
        if not isinstance(content, str) or not content.strip():
            return None
        status = raw.get("status")
        coerced: TodoStatus = status if status in _TODO_STATUSES else "pending"
        active = raw.get("activeForm")
        return TodoItem(
            content=content,
            status=coerced,
            active_form=active if isinstance(active, str) and active.strip() else None,
        )

    @property
    def summary(self) -> str:
        """A one-line progress digest — the ``DigestEntry.text`` fallback a
        role-unaware consumer (the ``OrderedDigest`` LLM-interpreter seam) reads:
        completed count over total, plus the item currently in progress if any."""
        done = sum(1 for i in self.items if i.status == "completed")
        head = f"{done}/{len(self.items)} done"
        active = next((i.content for i in self.items if i.status == "in_progress"), None)
        return f"{head} · {active}" if active else head


# The native tool names Claude Code's "Tasks" system uses to MUTATE
# its task list — Anthropic's split-call successor to ``TodoWrite`` on builds
# that ship it (verified on-host: ``TaskCreate``/``TaskUpdate``
# carry ``subject``/``description``/``activeForm``/``status``/``taskId`` —
# near-identical fields to ``TodoItem``). ``TaskList``/``TaskGet`` are
# read-only queries of the same board and surface no NEW state, so they stay
# unrecognized here (generic tool digest). ``TaskOutput``/``TaskStop`` are a
# DIFFERENT concept — background shell/agent process output and cancellation
# (confirmed via those tools' own on-host descriptions, unrelated to task
# tracking) — never part of this recognizer.
TASK_TOOL_NAMES: frozenset[str] = frozenset({"TaskCreate", "TaskUpdate"})

# A ``TaskCreate`` call never carries the id the server assigns it — the id
# rides back only in the call's own ``tool_result`` text, e.g. "Task #3
# created successfully: <subject>" (verified on-host).
_TASK_CREATED_RE = re.compile(r"Task #(\S+) created successfully")


@dataclass(slots=True)
class TaskBoard:
    """Session-scoped fold of Claude's incremental Task calls onto the
    existing :class:`TodoList` shape — the point is ZERO new wire surface or
    renderer. Unlike :meth:`TodoList.from_tool_call` (pure, one call → one
    list), Tasks split creation and mutation across separate calls correlated
    by a server-assigned id the call itself never carries (the id rides back
    only in ``TaskCreate``'s ``tool_result``, see :meth:`created_task_id`), so
    materializing "the list as it stands" needs a running fold across the
    whole transcript. This is that fold: a small mutable accumulator fed one
    call at a time, in transcript order, by an adapter's existing linear
    parse — not a second parser. Only ``TaskCreate``/``TaskUpdate`` reach it
    (see :data:`TASK_TOOL_NAMES`); everything about "what counts as a change"
    mirrors :meth:`TodoList._item`'s defensive coercion (an unreadable
    subject/status degrades the call to a no-op, never raises).
    """

    _order: list[str] = field(default_factory=list)
    _items: dict[str, TodoItem] = field(default_factory=dict)

    @staticmethod
    def created_task_id(result_text: str | None) -> str | None:
        """The server-assigned id from a ``TaskCreate`` call's own
        ``tool_result`` — the only place it appears. ``None`` on a missing or
        unexpectedly-shaped result; the caller then skips the create rather
        than guessing an id."""
        if not result_text:
            return None
        match = _TASK_CREATED_RE.match(result_text)
        return match.group(1) if match else None

    def create(self, task_id: str, raw_input: dict[str, Any]) -> bool:
        """Add (or replace) the task at ``task_id``. Returns whether the
        board actually changed — ``False`` on an unreadable subject, so the
        caller can fall back to a generic tool entry instead of a no-op
        snapshot."""
        subject = raw_input.get("subject")
        if not isinstance(subject, str) or not subject.strip():
            return False
        active = raw_input.get("activeForm")
        if task_id not in self._items:
            self._order.append(task_id)
        self._items[task_id] = TodoItem(
            content=subject,
            status="pending",
            active_form=active if isinstance(active, str) and active.strip() else None,
        )
        return True

    def update(self, task_id: str, raw_input: dict[str, Any]) -> bool:
        """Apply whichever of subject/status/activeForm the call carries to
        the task at ``task_id``. Returns whether anything changed — ``False``
        for an update to an id this board never saw created (the create call
        may be out of the loaded window, or its id never resolved) or a call
        with nothing readable to apply; either way the caller falls back to a
        generic tool entry rather than emitting a no-op snapshot. Fields this
        board doesn't model (``owner``, ``addBlocks``, ``addBlockedBy``) are
        deliberately dropped — :class:`TodoItem` has no equivalent and the
        existing card doesn't render them (YAGNI until a consumer needs it)."""
        current = self._items.get(task_id)
        if current is None:
            return False
        changes: dict[str, Any] = {}
        subject = raw_input.get("subject")
        if isinstance(subject, str) and subject.strip():
            changes["content"] = subject
        status = raw_input.get("status")
        if status in _TODO_STATUSES:
            changes["status"] = status
        active = raw_input.get("activeForm")
        if isinstance(active, str) and active.strip():
            changes["active_form"] = active
        if not changes:
            return False
        self._items[task_id] = replace(current, **changes)
        return True

    def snapshot(self) -> TodoList:
        """The board's current state as the shared card shape, in creation
        order — a task is never dropped once created, only its fields move."""
        return TodoList(items=tuple(self._items[tid] for tid in self._order if tid in self._items))

    def apply(self, name: str, block: ContentBlock, outcomes: Mapping[str, ToolOutcome]) -> bool:
        """Fold one ``TaskCreate``/``TaskUpdate`` tool-use block into this board.

        THE one create-vs-update dispatch every caller shares — the adapter's
        turn renderer (``claude_code.py::_assistant_entries``) and the
        ``latest_todo_from_messages`` projection both fold the identical
        transcript into a board and must not drift, so this lives on the board
        itself rather than duplicated (or left private to one caller).
        ``outcomes`` is the tool_use_id → :class:`ToolOutcome` map
        :func:`tool_outcomes` builds once per parse; it is where a
        ``TaskCreate``'s server-assigned id lives (see :meth:`created_task_id`),
        never rebuilt here. Returns whether the board
        actually changed — ``False`` (unreadable payload, unresolvable create
        id, update to an untracked id) tells the caller to fall back to a
        generic tool entry / skip the update instead of a no-op snapshot.
        """
        raw_input = block.tool_input
        if not isinstance(raw_input, dict):
            return False
        if name == "TaskCreate":
            outcome = outcomes.get(block.tool_use_id or "")
            task_id = self.created_task_id(outcome.text if outcome else None)
            return task_id is not None and self.create(task_id, raw_input)
        task_id = raw_input.get("taskId")
        return isinstance(task_id, str) and bool(task_id) and self.update(task_id, raw_input)


# How a compaction was triggered. Claude Code records this natively and
# authoritatively (``compactMetadata.trigger``, exactly ``manual``/``auto`` over
# 55 real boundaries on-host); Codex records NOTHING of the kind in any version
# from 0.93.0 to 0.147.0, which is why the field is nullable rather than
# defaulted — see :class:`CompactionBoundary`.
CompactionTrigger = Literal["manual", "auto"]


@dataclass(slots=True, frozen=True)
class CompactionBoundary:
    """The moment a harness replaced the conversation so far with a summary.

    The structured sibling of :class:`FileEdit` / :class:`TodoList` /
    :class:`AgentQuestion`, and the first one that is NOT derived from a tool
    call: a compaction is something the harness did to the context, so it
    arrives as its own native record rather than as an invocation. It rides the
    transcript as ``DigestEntry(role="compaction", compaction=…)`` so a reader
    sees where history was cut instead of a silent discontinuity — which,
    without a marker, reads as an agent that inexplicably forgot.

    Every field but ``summary`` is nullable, and each ``None`` is a different
    honest absence rather than one shared "unknown":

    * ``trigger`` — ``None`` means THIS HARNESS RECORDS NO TRIGGER, not that the
      trigger was unreadable. Codex persists no such field anywhere, so manual
      vs automatic is genuinely unknowable there and must render as absent; a
      guess would be indistinguishable from Claude's measured value.
    * ``dropped_tokens`` — tokens dropped BY THIS EVENT. It is a DELTA. Claude
      reports a session-running total (``cumulativeDroppedTokens``), so the
      adapter subtracts the previous boundary's total within the same file and
      the FIRST boundary of a file has no predecessor to subtract, hence
      ``None``. Carrying the raw total would silently inflate every later
      boundary by the whole session's history.
    * ``at`` — when the compaction happened, ``None`` for a record with no
      readable timestamp.

    ``summary`` is ``""`` (never ``None``) when the harness carries no readable
    summary text: Codex encrypts the replacement history and writes an empty
    ``payload.message`` on every one of 132 real records, so "no text here" is a
    measured fact about the format rather than a parse failure.
    """

    trigger: CompactionTrigger | None
    at: datetime | None
    dropped_tokens: int | None
    summary: str

    def headline(self) -> str:
        """The one-liner for ``DigestEntry.text`` — defined once so both adapters
        (and any future one) label a compaction identically for a role-unaware
        consumer. A harness with no trigger says only that it happened, because
        naming a trigger it never recorded would be a guess."""
        if self.trigger is None:
            return "Context compacted"
        return f"Context compacted ({'automatic' if self.trigger == 'auto' else 'manual'})"


# ── The agentic-loop spine ───────────────────────────────────────────────────
# ONE parse of a tool's transcript yields a tuple of these; :class:`SessionTurn`
# / :class:`OrderedDigest` below — and the downstream fleet / trace / final-result
# consumers — are all PROJECTIONS of this tuple, never a second parse of the raw
# transcript (DRY: one parser per adapter, many projections). The spine is
# provider-neutral and lineage-preserving: stable ids and the sidechain/thread
# fields survive normalization so a sub-agent tree can be reconstructed
# downstream without re-reading the native JSONL.

# Normalized role of one spine message. The adapter maps its native record onto
# exactly one of these. ``tool`` is a tool-RESULT carrier (a turn-loop step whose
# blocks resolve earlier tool calls, not a human turn); ``notification`` is an
# out-of-band notice the agent received (a delivered background-task result).
# ``compaction`` is the harness cutting the context out from under the loop —
# an event with no content of its own, whose payload rides
# :attr:`AgentMessage.compaction`. It is deliberately CONTENTLESS: every
# consumer that reads a message for text (the trace exporter's chat parts, the
# final-result scan) then skips it for free, so carrying the boundary on the
# spine changes no existing projection, while the turn/digest renderers that DO
# know the role place it in conversation order.
# A native record that is not a loop message (stream metadata, machinery,
# preamble) maps to no spine message at all — it is dropped, not carried.
MessageRole = Literal["user", "assistant", "tool", "notification", "compaction"]

# The content-block kinds a message carries, mirroring the provider's own block
# vocabulary so the mapping stays a mechanical translation. ``thinking`` is
# reasoning output (opaque or summarized); it rides the spine for faithfulness
# even where a given projection drops it.
ContentBlockType = Literal["text", "thinking", "tool_use", "tool_result"]


@dataclass(slots=True, frozen=True)
class TokenUsage:
    """Per-message token accounting, each field ``None`` when the provider did
    not report it — an absent count is NOT zero and is never fabricated.

    Claude reports ``input``/``output``/``cache_creation``/``cache_read`` per
    assistant message (no reasoning-token count → ``reasoning`` stays ``None``).
    Codex reports usage per SESSION (cumulative ``token_count`` events), not per
    message, so every Codex message's ``usage`` is ``None``; its session total —
    which alone carries ``reasoning`` — lives on :class:`AgentActivity`.
    """

    input: int | None = None
    output: int | None = None
    cache_creation: int | None = None
    cache_read: int | None = None
    reasoning: int | None = None


@dataclass(slots=True, frozen=True)
class ContentBlock:
    """One content block inside an :class:`AgentMessage`, provider-neutral.

    Mirrors the Anthropic / Codex block shape so the adapter mapping is a
    mechanical translation: ``type`` discriminates and each kind fills only the
    relevant fields — ``text`` / ``thinking`` → ``text``; ``tool_use`` →
    ``tool_name`` + ``tool_use_id`` (the call's id) + ``tool_input`` (its raw
    args, ``None`` when the call carried none); ``tool_result`` → ``tool_use_id``
    (the call it resolves) + ``text`` + ``is_error`` + ``duration_ms`` +
    ``exit_code``. A field irrelevant to the block kind stays ``None`` / ``False``.
    """

    type: ContentBlockType
    text: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    tool_input: dict[str, Any] | None = None
    is_error: bool = False
    duration_ms: int | None = None
    """The HARNESS'S OWN measured wall-clock time for this call, in milliseconds
    — set only on a ``tool_result`` where the provider records one natively
    (Codex ``exec_command``, from the paired ``event_msg`` ``exec_command_end``).
    ``None`` for Claude (which records no per-call duration on the result at
    all) and for a Codex tool_result the harness never paired one for — see
    agents/CLAUDE.md for the CLI-version gate. Distinct from
    :attr:`ToolCall.duration_ms`, which is ALWAYS derivable (message-timestamp
    diff) and falls back to that derivation when this is ``None``; this field
    exists so a more precise native measurement can override the derived one at
    the one place both are visible."""
    exit_code: int | None = None
    """The process's real exit status — set only on a Codex ``exec_command``
    ``tool_result`` from the same native ``exec_command_end`` record as
    :attr:`duration_ms`. ``None`` for Claude and for every Codex tool_result
    this harness never measured this way. Deliberately NOT folded into
    ``is_error``: Codex's own claim of "no structural tool-error flag" was
    scoped to ``function_call_output`` specifically and this is a genuinely
    different record, but flipping ``ToolCall.status`` on it is a larger,
    separate decision — see agents/CLAUDE.md."""


@dataclass(slots=True, frozen=True)
class AgentMessage:
    """One message/event in the agentic loop — the lineage-preserving spine.

    Stable ids survive normalization so a downstream consumer reconstructs the
    invocation tree without re-parsing the transcript:

    * ``message_id`` — the provider's logical message id (``None`` when it has
      none, e.g. Codex).
    * ``tool_use_id`` — the whole-message tool correlation: set on a
      ``notification`` to the spawning tool it reports on (so a fleet view closes
      the right in-flight id). Per-CALL and per-RESULT ids live on the
      :class:`ContentBlock`s, not here.
    * ``parent_tool_use_id`` — links a sub-agent message to the tool call that
      spawned it: Claude's on-disk ``sourceToolAssistantUUID`` (the streaming
      API's ``parent_tool_use_id``).

    ``usage`` / ``model`` are per-message and ``None`` when the record didn't
    carry them (never fabricated). ``is_sidechain`` marks a sub-agent-thread
    message and ``thread_id`` groups one sub-agent transcript (Claude's
    ``agentId``); a main-thread message leaves both unset.
    """

    role: MessageRole
    content: tuple[ContentBlock, ...] = ()
    message_id: str | None = None
    tool_use_id: str | None = None
    parent_tool_use_id: str | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    timestamp: datetime | None = None
    is_sidechain: bool = False
    thread_id: str | None = None
    compaction: CompactionBoundary | None = None
    """Set only on a ``compaction`` message — the boundary this event records.

    It cannot be derived from ``content`` the way the tool-call payloads are,
    because a compaction is not an invocation: there is no ``tool_use`` block to
    normalize, so the payload has to be carried. ``content`` stays empty on
    exactly these messages (see :data:`MessageRole`)."""

    sent_at: datetime | None = None
    """When a QUEUED message was submitted, which is not when it arrived.

    ``timestamp`` is when the harness DELIVERED the message into the loop, and
    that is the only instant the conversation's order can be built from. A
    message the user typed while the agent was busy sits in the harness's queue
    for as long as the agent takes — measured up to 42 s on real Claude
    transcripts — so its submit instant sorts back among the assistant work that
    answered the PREVIOUS prompt. Both facts are real and only one of them is an
    ordering key, hence two fields rather than a choice.

    ``None`` on every message that was not queued: the two instants coincide, so
    a second copy of ``timestamp`` would only invite a reader to believe the
    distinction was measured.
    """

    def text(self) -> str:
        """The human-readable text of this message — its ``text`` blocks joined
        (``thinking`` and tool blocks excluded). The user/notification projection
        line, and an assistant message's plain prose."""
        return "\n".join(b.text for b in self.content if b.type == "text" and b.text)

    def tool_names(self) -> tuple[str, ...]:
        """Names of the ``tool_use`` blocks this message carries, in order."""
        return tuple(b.tool_name for b in self.content if b.type == "tool_use" and b.tool_name)


# Whether a tool call has come back, and how. ``running`` is a FIRST-CLASS state
# rather than the absence of a result: a client picks a spinner over a checkmark
# from it, and "the call is still in flight" must be distinguishable from "this
# entry carries no tool metadata at all" (which is ``DigestEntry.tool is None``).
ToolCallStatus = Literal["running", "ok", "error"]


@dataclass(slots=True, frozen=True)
class ToolOutcome:
    """The ``tool_result`` that resolved one tool call — body, error flag, clock.

    The value type of the call→result map every transcript projection already
    built as ``dict[str, str | None]``. It carries two facts that map could not:
    ``is_error`` (a failed tool is not a successful one with sad text) and
    ``at``, the timestamp of the MESSAGE the result block rode in on — which is
    the only end-of-call clock either provider records.
    """

    text: str | None = None
    is_error: bool = False
    at: datetime | None = None
    duration_ms: int | None = None
    exit_code: int | None = None


def tool_outcomes(messages: Iterable[AgentMessage]) -> dict[str, ToolOutcome]:
    """The ONE ``tool_use_id`` → :class:`ToolOutcome` pre-scan, shared by every
    projection of the spine.

    A ``tool_result`` is a FORWARD reference — it always lands after the call it
    resolves, possibly turns later — so any consumer that needs a call's answer
    has to build this map first. Both adapters' turn renderers, the todo
    projection and (via :meth:`ToolCall.from_block`) the tool-call payload read
    it, so "what resolved this call" is defined exactly once.

    Membership, not truthiness, is the resolution test: a tool that returned
    nothing is present with ``text=None``, and that is a RESOLVED call. Deliberately
    NOT sidechain-filtered — a sub-agent's call resolves through its own result
    wherever it landed. Last write wins for a repeated id, matching the
    ``dict[str, str | None]`` scans this replaces.
    """
    out: dict[str, ToolOutcome] = {}
    for message in messages:
        for block in message.content:
            if block.type == "tool_result" and block.tool_use_id:
                out[block.tool_use_id] = ToolOutcome(
                    text=block.text,
                    is_error=block.is_error,
                    at=message.timestamp,
                    duration_ms=block.duration_ms,
                    exit_code=block.exit_code,
                )
    return out


@dataclass(slots=True, frozen=True)
class ToolCall:
    """One tool invocation as a renderer needs it: request, response, duration,
    and whether it is still running — the provider-neutral sibling of
    :class:`FileEdit` / :class:`TodoList` / :class:`AgentQuestion`, and the only
    one of the four that is not tied to a particular tool's payload shape.

    It rides EVERY entry a ``tool_use`` block produced (see
    :class:`DigestEntry`), including the structured ones, because "how long did
    this Edit take, and has it come back" is a question about the call rather
    than about the diff.

    ``status`` is decided at ONE place — :meth:`from_block` — off the single
    fact both harnesses agree on: a call with no resolving ``tool_result`` /
    ``function_call_output`` yet is IN FLIGHT. Codex writes its rollout
    record-by-record as the turn runs, and Claude flushes the assistant message
    carrying the ``tool_use`` before the result arrives, so the unresolved-call
    shape means the same thing in both files and no adapter needs its own rule.

    ``duration_ms`` pairs the call's own message timestamp with its result's,
    the same pairing ``usage/_intervals`` reduces over — including the clamp to
    zero, because the two endpoints are routinely written by two different
    clocks and an end before its start is a clock artefact, not negative work.
    ``None`` whenever either endpoint is missing (a running call, a provider
    that stamped no time) — an unmeasurable duration is absent, never zero.
    **A provider that measures the call itself wins over this derivation**:
    when the resolving :class:`ToolOutcome` carries its own ``duration_ms``
    (Codex ``exec_command``, from the paired native ``exec_command_end``
    record — see agents/CLAUDE.md), that value is used instead of the
    message-timestamp diff, because it is the harness's own wall-clock
    measurement rather than a proxy that also bills message-write latency.

    ``exit_code`` rides the same native measurement, purely informational: it
    does NOT feed ``status`` (see agents/CLAUDE.md on why Codex's own claim of
    "no structural tool-error flag" stays true for ``status`` even though this
    field exists) — ``None`` unless the provider recorded one.
    """

    name: str
    tool_use_id: str
    status: ToolCallStatus
    input: dict[str, Any] | None = None
    result: str | None = None
    duration_ms: int | None = None
    exit_code: int | None = None

    @classmethod
    def from_block(
        cls,
        block: ContentBlock,
        outcomes: Mapping[str, ToolOutcome],
        *,
        called_at: datetime | None = None,
    ) -> ToolCall:
        """Normalize one ``tool_use`` block plus its resolution into a call.

        ``called_at`` is the timestamp of the message the block rode in on —
        several parallel calls in one assistant message legitimately share it,
        which is honest: they were issued together. Correlation is by
        ``tool_use_id``, so concurrent calls never blur into each other; an
        id-less block (neither harness emits one, but the spine allows it) can
        never correlate and therefore reads ``running`` forever, exactly as an
        unanswered call does.
        """
        call_id = block.tool_use_id or ""
        outcome = outcomes.get(call_id)
        if outcome is None:
            return cls(
                name=block.tool_name or "",
                tool_use_id=call_id,
                status="running",
                input=block.tool_input,
            )
        return cls(
            name=block.tool_name or "",
            tool_use_id=call_id,
            status="error" if outcome.is_error else "ok",
            input=block.tool_input,
            result=outcome.text,
            duration_ms=(
                outcome.duration_ms
                if outcome.duration_ms is not None
                else _elapsed_ms(called_at, outcome.at)
            ),
            exit_code=outcome.exit_code,
        )


def _elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    """Milliseconds between two transcript timestamps, clamped at zero."""
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() * 1000))


@dataclass(slots=True, frozen=True)
class FinalResult:
    """A session's terminal outcome — the last assistant turn plus whether it
    is truly final, best-effort like every read here.

    ``text`` is the tail assistant message's joined text (may be empty when
    that message is a bare tool call with no prose); ``is_complete`` says
    whether that message is the *actual* tail of the loop with no further
    tool call pending — i.e. the provider-agnostic equivalent of Claude's
    ``stop_reason in (end_turn, stop_sequence)`` / Codex's matched
    ``task_started``/``task_complete`` pair, derived from message SHAPE so no
    second parser is needed. ``False`` covers both "still working" (a trailing
    tool call) and "blocked on the human" (a trailing unanswered
    question/plan-approval tool_use) — either way, not a final answer yet.
    """

    text: str
    is_complete: bool
    message_id: str | None = None
    timestamp: datetime | None = None


def final_result_from_messages(messages: tuple[AgentMessage, ...]) -> FinalResult | None:
    """Project the agentic-loop spine onto its :class:`FinalResult`, or
    ``None`` when no assistant has replied yet (session just started).

    A PROJECTION of :meth:`AgentMessage`-shaped output only — it re-parses
    nothing. Walks the already-normalized, time-ordered messages once,
    skipping sub-agent (sidechain) traffic (a fleet's answer is not the main
    thread's), and keeps the last main-thread message (``tail``) alongside
    the last ``assistant`` one (``last_assistant``, possibly earlier than
    ``tail`` when a tool result or a fresh human turn follows it). The session
    is complete only when those coincide AND the assistant message carries no
    ``tool_use`` block — the same "open tool call" shape that (for Claude)
    coincides exactly with ``stop_reason == "tool_use"``, and (for Codex) with
    an in-flight ``function_call``/``custom_tool_call`` — so one shape check
    serves both providers without threading a provider-specific field onto
    the spine.
    """
    tail: AgentMessage | None = None
    last_assistant: AgentMessage | None = None
    for message in messages:
        if message.is_sidechain:
            continue
        tail = message
        if message.role == "assistant":
            last_assistant = message
    if last_assistant is None:
        return None
    open_tool_use = any(block.type == "tool_use" for block in last_assistant.content)
    return FinalResult(
        text=last_assistant.text(),
        is_complete=tail is last_assistant and not open_tool_use,
        message_id=last_assistant.message_id,
        timestamp=last_assistant.timestamp,
    )


def latest_todo_from_messages(messages: tuple[AgentMessage, ...]) -> TodoList | None:
    """Project the agentic-loop spine onto the agent's CURRENT todo/checklist
    state, or ``None`` when no todo/Task tool has been called yet.

    The ``latest_todo`` sibling of :func:`final_result_from_messages` — same
    recipe (one pass over the already-normalized spine, zero re-parsing),
    same module, same ownership shape. It reuses the exact fold
    ``claude_code.py``'s turn renderer already performs: a whole-list call
    (``TodoWrite``/``update_plan``, see :meth:`TodoList.recognizes`) simply
    replaces the running answer; a split-call ``TaskCreate``/``TaskUpdate``
    (see :data:`TASK_TOOL_NAMES`) folds onto one running :class:`TaskBoard`
    via :meth:`TaskBoard.apply` — the SAME board instance for the whole
    spine, because a ``TaskUpdate`` near the end of a long session can
    reference an id whose ``TaskCreate`` (and the ``tool_result`` carrying its
    server-assigned id) sits arbitrarily far back. This is exactly why a
    "last N turns" tail read is wrong here: pruning the transcript before the
    fold would drop the create the later update depends on. ``latest`` tracks
    whichever recognized call came LAST in transcript order — a session that
    (implausibly) mixed both styles still reports the true final state, not
    just whichever style happened to run second in this function's checks.

    Sidechain (sub-agent) traffic is skipped for the fold itself, exactly
    like :func:`final_result_from_messages` — a sub-agent's checklist is not
    the main thread's. The ``tool_result`` pre-scan, however, is NOT
    sidechain-filtered, mirroring ``claude_code.py::turns``'s ``answered``
    map: a ``TaskCreate`` in a sub-agent thread still resolves through its own
    ``tool_result`` wherever it landed.
    """
    outcomes = tool_outcomes(messages)

    board = TaskBoard()
    latest: TodoList | None = None
    for message in messages:
        if message.is_sidechain or message.role != "assistant":
            continue
        for block in message.content:
            if block.type != "tool_use" or not block.tool_name:
                continue
            name = block.tool_name
            if TodoList.recognizes(name):
                todos = TodoList.from_tool_call(name, block.tool_input)
                if todos:
                    latest = todos[0]
            elif name in TASK_TOOL_NAMES and board.apply(name, block, outcomes):
                latest = board.snapshot()
    return latest


@dataclass(slots=True, frozen=True)
class DigestEntry:
    """One line of an :class:`OrderedDigest`: a role tag plus a short summary.

    ``question``/``file_edit``/``todo``/``compaction`` are populated *only* for
    their matching role (``"question"`` / ``"file_edit"`` / ``"todo"`` /
    ``"compaction"``) — the structured payload the transcript renderers (TUI +
    webapp) draw as a choice card, a diff card, a checklist card, or a
    history-was-cut marker; for every other role all four are ``None`` and
    ``text`` carries the line. Each structured kind keeps ``text`` set to a
    sensible one-liner (a question's prompt, an edit's ``f"{tool_name} {path}"``,
    a todo list's progress ``summary``, a boundary's
    :meth:`CompactionBoundary.headline`) so a role-unaware consumer (the
    ``OrderedDigest`` LLM-interpreter seam) still reads something sensible.

    ``tool`` is the ODD ONE OUT and deliberately so: it is set on EVERY entry a
    ``tool_use`` block produced — the generic ``"tool"`` line *and* the three
    structured cards above — because request, response, duration and
    still-running are facts about the invocation, not about the payload one
    particular tool happens to carry. That is what lets a renderer draw one
    expander with a spinner-or-check for every tool call, whatever card sits
    inside it. ``None`` means this entry did not come from a tool call at all,
    or the provider surfaces no per-call detail (mewbo's remote timeline) —
    never "still running", which is :attr:`ToolCall.status`'s job.
    """

    role: Literal[
        "user",
        "assistant",
        "tool",
        "summary",
        "status",
        "notification",
        "question",
        "file_edit",
        "todo",
        "compaction",
    ]
    text: str
    question: AgentQuestion | None = None
    file_edit: FileEdit | None = None
    todo: TodoList | None = None
    tool: ToolCall | None = None
    compaction: CompactionBoundary | None = None


@dataclass(slots=True, frozen=True)
class OrderedDigest:
    """Compact, ordered slice of a transcript for the future LLM interpreter.

    The ``USER → ASSISTANT → TOOL(name) → summary`` skeleton with bulky
    ``tool_result`` payloads stripped — small enough to feed an external model
    cheaply. A designed seam only: nothing in the MVP calls an LLM with it.
    """

    entries: tuple[DigestEntry, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.entries


@dataclass(slots=True, frozen=True)
class QueuedMessage:
    """One message a harness is holding until it can inject it.

    In-process state, so a dataclass: the wire mirror is
    ``contracts.sessions.QueuedMessageView``. Every field is READ from the
    harness's own record — Grove does not own the queue and keeps no second one,
    because the harness is what drains it and a Grove-side ledger would be a
    second writer with no arbitration, drifting the moment somebody types
    straight into the pane.
    """

    text: str
    sent_at: datetime | None = None
    """When it was SUBMITTED, never when it will be delivered. ``None`` when the
    harness recorded no instant."""

    position: int = 0
    """Place in the queue as the harness currently reports it, 0 first — what it
    says now, not a promise about delivery order."""


@dataclass(slots=True, frozen=True)
class SessionTurn:
    """One conversation turn: a human prompt plus everything until the next one.

    ``entries`` carries the assistant replies and tool calls inside the turn as
    :class:`DigestEntry` rows (full text, not the digest's truncated form).
    ``user_text`` is empty for a leading continuation block — assistant records
    that precede any human turn in the file (a resumed/compacted session).

    The turn carries two clocks and they answer different questions.
    ``started_at`` is when the prompt reached the agent, which is what orders
    the conversation; ``sent_at`` is when a QUEUED prompt was submitted, and it
    is set only for those (see ``AgentMessage.sent_at``).
    """

    user_text: str
    started_at: datetime | None = None
    entries: tuple[DigestEntry, ...] = ()
    sent_at: datetime | None = None


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
    # When the session was BORN (its first record's timestamp) — distinct from
    # ``last_event_at``. This is what a workspace's created_at is compared
    # against to decide whether a *discovered* (non-minted) session actually
    # belongs to it (`WorkspaceState.adopts_session`, see grove.core.CLAUDE.md)
    # — mtime keeps advancing on a stale file that merely gets touched, but
    # birth is immutable. Populated by the filesystem adapters that already
    # compute it for `SessionSummary.created_at` (claude_code, codex); remote
    # (mewbo) and generic adapters leave it ``None`` (never discovery-adopted).
    started_at: datetime | None = None
    error_detail: str | None = None
    # Reserved seam for the future external-LLM task interpreter. The
    # adapter's `transcript_digest()` produces the compact, tool_result-stripped
    # slice an external model would read; a user-configured `InterpreterService`
    # would populate this with a one-line human summary. Off by default and not
    # wired in the MVP — the field reserves the dashboard space so adding the
    # interpreter later needs no contract change (YAGNI: seam now, call later).
    interpreted_status: str | None = None
    # The questions the agent is asking RIGHT NOW. A single ``AskUserQuestion`` /
    # ``request_user_input`` call carries several questions answered atomically,
    # so the whole group rides together, ordered as asked; empty when nothing is
    # pending. It carries the pending ask onto the live activity stream so a
    # client can render an answer affordance the instant the question appears,
    # instead of only after the terminal resolved it.
    #
    # WHICH SOURCE fills it is a property of the provider, not of this field, and
    # the two providers sit at opposite ends: Claude Code flushes NOTHING to the
    # transcript while a question is on screen, so only the ask-time hook sidecar
    # can see one (the Claude parser leaves this empty and the ``ActivityService``
    # fills it). Codex has no hook mechanism at all, but writes its rollout
    # record-by-record as the turn runs — measured live on codex-cli 0.147.0 — so
    # its unanswered ``request_user_input`` call is visible in the file and the
    # CODEX PARSER fills this directly. The service prefers a sidecar capture and
    # otherwise passes the parser's answer through.
    questions: tuple[AgentQuestion, ...] = ()

    @property
    def needs_attention(self) -> bool:
        """True when the state is one that wants the human (epic §6)."""
        return self.state in ATTENTION_STATES

    @classmethod
    def empty(cls, state: AgentActivityState = AgentActivityState.UNKNOWN) -> AgentActivity:
        """An activity with no metrics — for an unreadable or not-yet-written transcript."""
        return cls(state=state)


@dataclass(slots=True, frozen=True)
class SessionRef:
    """A minimal pointer to one session anywhere in an adapter's store — the
    unit :meth:`~grove.core.agents.base.AgentAdapter.discover_all` returns.

    Built from exactly one bounded head read per session (never a full parse
    — see the adapter's ``discover_all`` docstring for why). Deliberately
    thinner than :class:`SessionSummary`: a host-wide scan has no cwd to bind
    to yet, so ``cwd``/``git_branch`` are honestly ``None`` for the ~2 % of
    Claude transcripts whose head read never reveals a cwd, rather than the
    row being dropped. ``transcript_path`` is ``None`` for a remote-backed
    session (mewbo — no local file, mirroring ``SessionSummary``).

    ``size_bytes`` rides the SAME ``stat()`` call every filesystem adapter
    already makes for ``mtime`` — ``st_size`` sits on the same ``stat_result``
    at zero extra I/O, so a filesystem adapter should always set it. ``None``
    means either a remote-backed session (no local file to stat) or a stat
    that failed (vanished file) — never fabricate a zero.
    """

    session_id: str
    adapter_kind: str
    cwd: str | None
    transcript_path: Path | None
    birth: datetime | None
    mtime: float
    git_branch: str | None = None
    size_bytes: int | None = None


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


# What kind of input control this is. Drives only *display grouping* on the wire
# — the trigger verbs act on the ``name``, not the kind — so it stays a Literal.
ControlKind = Literal["command", "skill", "mcp_server"]

# Where a scanned control was found. A display-only origin hint (a project
# worktree's ``.claude/``, the user config dir, a tool built-in); never drives
# behaviour, so a Literal closed set is enough.
ControlScope = Literal["project", "user", "builtin", "dynamic"]


@dataclass(slots=True, frozen=True)
class SessionControl:
    """One invokable session input-affordance — a slash command, a skill, or a
    configured MCP server.

    ``name`` is what a trigger delivers (a command/skill is invoked as ``/name``);
    ``scope`` is a display-only origin hint; ``detail`` is an optional one-line
    description or origin basename read straight off disk (a command's
    front-matter, a skill's summary), never fabricated — ``None`` when the source
    carries none.
    """

    name: str
    scope: ControlScope = "project"
    detail: str | None = None


@dataclass(slots=True, frozen=True)
class SessionControls:
    """The input-control surface a session exposes — a TIER 1 filesystem
    enumeration, cheap enough to answer with NO running session.

    Two ownership halves, composed at the manager seam so neither is re-derived
    elsewhere: an adapter's :meth:`AgentAdapter.session_controls` fills only the
    filesystem-scanned lists (``commands`` / ``skills`` / ``mcp_servers`` — the
    provider knows where ``.claude/commands`` etc. live); the composing manager
    adds the config-derived ``models`` (via ``registry.resolve_models``),
    ``current_model``, and ``permission_mode``. Best-effort like every read here:
    each tuple is empty (and each scalar ``None``) when the tool has no such
    surface or nothing is on disk — never raises.
    """

    commands: tuple[SessionControl, ...] = ()
    skills: tuple[SessionControl, ...] = ()
    mcp_servers: tuple[SessionControl, ...] = ()
    models: tuple[str, ...] = ()
    current_model: str | None = None
    permission_mode: str | None = None

    @classmethod
    def empty(cls) -> SessionControls:
        """A control surface with nothing enumerated — the honest answer for a
        tool with no local control surface, or when a scan fails."""
        return cls()
