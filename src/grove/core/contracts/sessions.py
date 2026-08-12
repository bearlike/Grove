"""Wire shapes for agent-session exploration — Pydantic mirrors of the explorer IR.

The daemon serializes ``SessionExplorer`` *and* ``SessionCatalog`` output
through these (``GET /workspaces/{id}/sessions``, ``.../sessions/{sid}/turns``,
and the project-or-host-scoped ``GET /sessions`` + ``/sessions/{sid}/turns``).
Session history is **fetch-on-demand only**: these shapes are never embedded in
the SSE ``DashboardEvent`` — turns are unbounded where the live dashboard
payload must stay small. Same ``from_*`` + ``frozen=True`` pattern as
``activity.py``, with the engine dataclasses imported under ``TYPE_CHECKING``
only.

``transcript_path`` never crosses the wire — a client identifies a session by
id, and the file layout is host-private. The session's ``cwd`` and its resolved
``repo_root`` DO cross (Session Catalog): the views-never-expose rule is about
*transcript* paths, `WorkspaceStateView.worktree_path` already carries a
working directory, and "where did this session happen" is the entire value of a
host-wide catalog row — a row you can't place is a row you can't use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.contracts.activity import AgentActivityView
from grove.core.contracts.questions import (
    _ENTRY_TEXT_CAP,
    AgentQuestionView,
    _truncate,
)
from grove.core.contracts.usage import DurationView

if TYPE_CHECKING:
    from grove.core.agents import (
        CompactionBoundary,
        DigestEntry,
        FileEdit,
        QueuedMessage,
        SessionControl,
        SessionControls,
        SessionTurn,
        TodoList,
        ToolCall,
    )
    from grove.core.sessions import CatalogEntry, ProjectContext, SessionListing

# A diff is legitimately bigger than a chat line — a generous ceiling well
# above ``_ENTRY_TEXT_CAP`` (4000) so an ordinary file edit never truncates,
# while still bounding the pathological case (a multi-MB generated file).
_FILE_EDIT_TEXT_CAP = 100_000

# A tool body sits BETWEEN the two, and the reason is multiplicity rather than
# size: a turn holds one or two diffs and routinely holds dozens of tool calls,
# so this cap multiplies where ``_FILE_EDIT_TEXT_CAP`` does not. 16 KB carries an
# ordinary ``Read`` of a few hundred lines or a long ``Bash`` output whole, and
# bounds a 40-call turn at ~1.3 MB instead of ~8 MB. Applied to the result body
# and, recursively, to every string inside the request — a tool's bulk is always
# in a string value (a file body, a patch, a command), never in its structure.
_TOOL_BODY_CAP = 16_000

# Mirrors ``grove.core.agents.TodoStatus``. Duplicated (not imported) so the
# contracts package never drags the engine in at runtime — the webapp codegen
# drift-check is the guard, exactly like ``AgentQuestionKind`` in ``questions.py``.
TodoStatus = Literal["pending", "in_progress", "completed"]

# Mirrors ``grove.core.agents.CompactionTrigger``, duplicated for the same
# reason. Nullable everywhere it is used: Claude Code records the trigger
# natively, Codex records none at all, and "this harness does not say" must stay
# distinguishable from either value.
CompactionTrigger = Literal["manual", "auto"]


class RemapSessionRequest(BaseModel):
    """Body for ``POST /workspaces/{id}/session`` — pin an existing agent session
    as a workspace's tracked primary.

    ``session_ref`` is a session id or a unique id-prefix, resolved through the
    workspace's project scope (the same resolution ``grove sessions show``
    accepts). Lives here beside ``SessionSummaryView`` — it is a session-domain
    write, not part of the create-workspace shape — mirroring the way
    ``QuestionAnswerRequest`` sits with the question views. The daemon answers
    with the updated ``WorkspaceStateView``, like the other mutation verbs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_ref: str = Field(min_length=1)
    """A full agent-session id or a unique id-prefix within the project."""


class FileEditView(BaseModel):
    """Wire mirror of ``grove.core.agents.FileEdit`` — one file mutation a tool
    call performed, normalized across providers so the webapp/TUI can draw a
    diff card instead of a bare tool name. ``old_text`` is empty for a
    from-scratch write; never backfilled from disk (see the engine dataclass's
    docstring for why).

    ``path`` is the full path as the tool recorded it (usually absolute — the
    tooltip target). ``display_path`` is that path made relative to the
    session's cwd (the worktree the agent ran in) for the diff-card header, so
    the reader sees ``src/foo.py`` not ``/home/…/.worktrees/x/src/foo.py``. The
    anchor is host-private and stays engine-side — only the two rendered paths
    cross the wire. ``display_path`` falls back to the full path when the edit
    is outside the anchor or no cwd was recorded.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    display_path: str
    old_text: str
    new_text: str

    @classmethod
    def from_edit(cls, e: FileEdit, anchor: str | None = None) -> FileEditView:
        return cls(
            path=e.path,
            display_path=cls._relativize(e.path, anchor),
            old_text=_truncate(e.old_text, _FILE_EDIT_TEXT_CAP),
            new_text=_truncate(e.new_text, _FILE_EDIT_TEXT_CAP),
        )

    @staticmethod
    def _relativize(path: str, anchor: str | None) -> str:
        """The path relative to ``anchor`` (the session cwd), or the path
        unchanged when it isn't under the anchor / no anchor was recorded.
        Lexical (``relative_to`` walks path segments, so ``/a/bc`` is not under
        ``/a/b``); never touches the filesystem."""
        if not path or not anchor:
            return path
        try:
            return str(PurePosixPath(path).relative_to(PurePosixPath(anchor)))
        except ValueError:
            return path


class TodoItemView(BaseModel):
    """Wire mirror of ``grove.core.agents.TodoItem`` — one entry in an agent's
    todo/plan list. ``content`` is the imperative task text; ``active_form`` is
    Claude's optional present-tense phrasing (``None`` for Codex ``update_plan``,
    which carries no equivalent)."""

    model_config = ConfigDict(frozen=True)

    content: str
    status: TodoStatus
    active_form: str | None = None


class TodoListView(BaseModel):
    """Wire mirror of ``grove.core.agents.TodoList`` — the current todo/plan list
    an agent drove through ``TodoWrite`` (Claude) / ``update_plan`` (Codex),
    normalized so the webapp/TUI draw one checklist card instead of a bare tool
    name. The todo sibling of ``FileEditView``/``AgentQuestionView`` — a third
    structured ``DigestEntryView`` payload proving the "new role + optional
    payload" recipe generalizes with zero daemon-route changes."""

    model_config = ConfigDict(frozen=True)

    items: list[TodoItemView] = []

    @classmethod
    def from_todo(cls, t: TodoList) -> TodoListView:
        return cls(
            items=[
                TodoItemView(
                    content=_truncate(i.content, _ENTRY_TEXT_CAP),
                    status=i.status,
                    active_form=i.active_form,
                )
                for i in t.items
            ]
        )


class CompactionView(BaseModel):
    """Wire mirror of ``grove.core.agents.CompactionBoundary`` — where the
    harness replaced the conversation so far with a summary.

    The fourth structured ``DigestEntryView`` payload, and the first that comes
    from a native harness record rather than a tool call. Each nullable field is
    a distinct honest absence and a client must not collapse them:
    ``trigger: null`` means THIS HARNESS RECORDS NO TRIGGER (Codex records none
    in any version), never that it defaulted to automatic;
    ``dropped_tokens: null`` means the harness published no per-event count, and
    when it is a number it is a DELTA for this one compaction — never the
    session-running total Claude Code's transcript actually stores.

    ``summary`` is ``""`` (never null) when the harness carries no readable
    replacement text — Codex encrypts it — and is capped at the shared
    ``_ENTRY_TEXT_CAP`` rather than given a diff-sized ceiling of its own: a real
    summary measured 13.9-55.3 KB on-host, and this rides the same per-turn
    payload as every other entry, so an uncapped one would ship tens of KB per
    boundary for text a reader skims.
    """

    model_config = ConfigDict(frozen=True)

    trigger: CompactionTrigger | None = None
    at: datetime | None = None
    dropped_tokens: int | None = None
    summary: str = ""

    @classmethod
    def from_compaction(cls, c: CompactionBoundary) -> CompactionView:
        return cls(
            trigger=c.trigger,
            at=c.at,
            dropped_tokens=c.dropped_tokens,
            summary=_truncate(c.summary, _ENTRY_TEXT_CAP),
        )


def _cap_strings(value: Any, cap: int) -> tuple[Any, bool]:
    """*value* with every string inside it truncated to *cap*, plus whether
    anything was trimmed.

    A tool's request is arbitrary provider JSON, so the bound has to be applied
    through the structure rather than to a serialized blob — a client renders
    the arguments as fields, and collapsing them to a capped string would trade
    one honest bound for an unreadable payload. Non-string leaves pass through:
    their size is bounded by the structure, and rewriting a number would be
    changing the request rather than trimming it.
    """
    if isinstance(value, str):
        return (value, False) if len(value) <= cap else (_truncate(value, cap), True)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        trimmed = False
        for key, item in value.items():
            capped, hit = _cap_strings(item, cap)
            out[str(key)] = capped
            trimmed = trimmed or hit
        return out, trimmed
    if isinstance(value, list):
        items: list[Any] = []
        trimmed = False
        for item in value:
            capped, hit = _cap_strings(item, cap)
            items.append(capped)
            trimmed = trimmed or hit
        return items, trimmed
    return value, False


class ToolCallView(BaseModel):
    """Wire mirror of ``grove.core.agents.ToolCall`` — one tool invocation's
    request, response, duration and running state.

    Rides EVERY ``DigestEntryView`` that came from a tool call, structured cards
    included, because a spinner-or-check and an elapsed time are facts about the
    invocation rather than about the card inside it.

    ``status`` is the render fork: ``running`` means the transcript holds this
    call with no result yet, which is a first-class state and not the absence of
    ``result`` — a settled call that returned nothing also has ``result: null``.
    ``tool_use_id`` is the correlation key, so several calls issued in one
    assistant turn stay individually addressable however they interleave.

    Both bodies are BOUNDED and say so: ``input_truncated`` / ``result_truncated``
    are explicit rather than left to the trailing ellipsis ``_truncate`` writes,
    because an ellipsis inside a command's own output is indistinguishable from
    output the tool actually produced — the one place this package's usual trim
    signal is not enough.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    tool_use_id: str
    status: Literal["running", "ok", "error"]
    input: dict[str, Any] | None = None
    input_truncated: bool = False
    result: str | None = None
    result_truncated: bool = False
    duration_ms: int | None = None

    @classmethod
    def from_call(cls, c: ToolCall) -> ToolCallView:
        capped_input, input_truncated = _cap_strings(c.input, _TOOL_BODY_CAP)
        result = None if c.result is None else _truncate(c.result, _TOOL_BODY_CAP)
        return cls(
            name=c.name,
            tool_use_id=c.tool_use_id,
            status=c.status,
            input=capped_input,
            input_truncated=input_truncated,
            result=result,
            result_truncated=result is not None and result != c.result,
            duration_ms=c.duration_ms,
        )


class DigestEntryView(BaseModel):
    """Wire mirror of ``grove.core.agents.DigestEntry`` (text capped).

    ``question``/``file_edit``/``todo``/``compaction`` are set only for their
    matching role (``"question"``/``"file_edit"``/``"todo"``/``"compaction"``) —
    the structured payload; every other role leaves all four ``None`` and reads
    from ``text``. ``compaction`` additionally rides only the fetched TURN view:
    the digest projection carries the same role with a ``null`` payload, because
    a compaction summary is the largest single thing a transcript holds.

    ``tool`` is set for every entry a tool call produced, whatever its role —
    including the three structured ones, which describe a payload and say
    nothing about the invocation that carried it. ``None`` means this entry did
    not come from a tool call (prose, a notification) or the provider surfaces
    no per-call detail; it never means "still running", which is
    ``ToolCallView.status``.
    """

    model_config = ConfigDict(frozen=True)

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
    question: AgentQuestionView | None = None
    file_edit: FileEditView | None = None
    todo: TodoListView | None = None
    tool: ToolCallView | None = None
    compaction: CompactionView | None = None

    @classmethod
    def from_entry(cls, e: DigestEntry, anchor: str | None = None) -> DigestEntryView:
        return cls(
            role=e.role,
            text=_truncate(e.text, _ENTRY_TEXT_CAP),
            question=AgentQuestionView.from_question(e.question) if e.question else None,
            file_edit=FileEditView.from_edit(e.file_edit, anchor) if e.file_edit else None,
            todo=TodoListView.from_todo(e.todo) if e.todo else None,
            tool=ToolCallView.from_call(e.tool) if e.tool else None,
            compaction=CompactionView.from_compaction(e.compaction) if e.compaction else None,
        )


class SessionTurnView(BaseModel):
    """Wire mirror of ``grove.core.agents.SessionTurn``.

    ``user_text`` is empty for a leading continuation block (a resumed or
    compacted session's head) — same convention as the engine dataclass.
    """

    model_config = ConfigDict(frozen=True)

    user_text: str
    started_at: datetime | None
    entries: list[DigestEntryView]

    sent_at: datetime | None = None
    """When the prompt was SUBMITTED, for a turn that waited in the harness's
    queue — never when it arrived, which is ``started_at``.

    A message typed at a busy agent sits queued until the harness injects it
    (measured up to 42 s), so the two clocks genuinely differ and only
    ``started_at`` orders the conversation. ``None`` on every ordinary turn: the
    two instants coincide there, and a duplicated value would invite a client to
    render a wait that was never measured. Defaulted, so a client built before
    this field decodes unchanged."""

    @classmethod
    def from_turn(cls, t: SessionTurn, anchor: str | None = None) -> SessionTurnView:
        return cls(
            user_text=_truncate(t.user_text, _ENTRY_TEXT_CAP),
            started_at=t.started_at,
            entries=[DigestEntryView.from_entry(e, anchor) for e in t.entries],
            sent_at=t.sent_at,
        )


class SessionProjectView(BaseModel):
    """Wire mirror of ``grove.core.sessions.ProjectContext`` — the repo a
    catalog row's ``cwd`` resolves to.

    Carries no branch on purpose: the branch that belongs on a session row is
    the one the SESSION recorded (``SessionSummaryView.git_branch``), not
    whatever the worktree happens to be checked out to now. Reaches the wire
    only through ``SessionSummaryView.project``, so it is not re-exported from
    the package — the ``FileEditView``/``TodoListView`` precedent.
    """

    model_config = ConfigDict(frozen=True)

    repo_root: str
    repo_name: str
    is_worktree: bool
    is_grove_managed: bool

    @classmethod
    def from_project(cls, p: ProjectContext) -> SessionProjectView:
        return cls(
            repo_root=str(p.repo_root),
            repo_name=p.repo_name,
            is_worktree=p.is_worktree,
            is_grove_managed=p.is_grove_managed,
        )


class SessionSummaryView(BaseModel):
    """One session row — wire mirror of ``SessionListing`` (project scope) and
    of ``CatalogEntry`` (host scope, the Session Catalog).

    ONE view for both scopes rather than a parallel catalog shape: the two
    engine rows answer the same question at different widths, and a second view
    would force every client to branch on which listing it fetched. What the
    wider scope cannot honestly know is ``None`` rather than fabricated —
    ``activity`` comes from a full transcript parse the host-wide scan
    deliberately never pays (it is metadata-only, one bounded head read per
    session), so a catalog row leaves it null. Read it as "not parsed at this
    scope", never as "zero". ``turn_count`` and ``duration`` are the fields
    that escape that rule at host scope, because they are not parsed there
    either: both are READ from one durable cache a background pass fills, from
    one parse per changed transcript (see the fields). ``size_bytes`` is a
    THIRD escape, for a weaker reason worth knowing: every filesystem
    adapter's ``discover_all`` already ``stat()``s each transcript for its
    ``mtime``, and ``st_size`` sits on that same ``stat_result`` — filling it
    costs no I/O at all, only a field on ``SessionRef`` (see
    ``SessionRef.size_bytes``). It is still nullable: a remote-backed session
    has no local file to stat, and a vanished file fails the stat outright.

    The ``workspace_*`` trio is ``None`` for a hand-staged session (a directory
    Grove doesn't manage); ``workspace_branch`` is additionally ``None`` on
    every catalog row (the host scan annotates a workspace, not its branch).
    ``project``/``live`` are the fields the catalog adds and the project-scoped
    listing leaves at their defaults.
    """

    model_config = ConfigDict(frozen=True)

    session_id: str
    adapter_kind: str
    provenance: str
    primary: bool = False
    workspace_id: str | None
    workspace_title: str | None
    workspace_branch: str | None
    git_branch: str | None
    created_at: datetime | None
    modified_at: datetime | None
    size_bytes: int | None
    title: str | None
    first_prompt: str | None
    last_prompt: str | None
    activity: AgentActivityView | None
    cwd: str | None = None
    """The directory the session ran in — the coordinate the turns endpoint
    resolves a workspace-less row by, and what makes a catalog row placeable.
    ``None`` when the head read never recovered one (~2 % of Claude
    transcripts); such a row cannot be drilled into."""
    project: SessionProjectView | None = None
    """The repo enclosing ``cwd``; ``None`` when no git repo does."""
    live: bool = False
    """A same-kind agent process is running in ``cwd`` AND this transcript is
    fresh — an honest directory-level signal, never a claim that a specific
    process owns this specific session (several agents routinely share one
    cwd). Always ``False`` outside the catalog scope, which is the only reader
    that pays for the ``/proc`` scan."""
    duration: DurationView | None = None
    """How long this session WORKED, as the two numbers that are not the same.

    ``active_ms`` is the union of its active intervals — real elapsed time with
    the waits for a human removed — and ``execution_ms`` is those same intervals
    SUMMED across the root agent and every sub-agent, so ten sub-agents running
    ten minutes side by side report 10m and 100m. The divergence is the
    measurement, not a double count, which is why one number could never serve.

    ``None`` follows ``turn_count``'s rule and not ``activity``'s: it means "not
    timed yet", never "did no work". A session that genuinely did nothing
    measurable reports a ``DurationView`` whose fields are themselves null.
    Never render a null as ``0`` — this column is read as evidence of cost.
    """

    turn_count: int | None = None
    """How many conversation turns the session holds — the "is this worth
    opening" signal, at the ONE field a client reads at either scope.

    Hoisted out of ``activity.human_turns`` (the same number, from the same
    parse) precisely because ``activity`` is null at catalog scope: a client
    reaching into a nullable sub-view for a scalar has to know which listing it
    fetched, which is the branch this one-view-for-two-scopes shape exists to
    remove. ``None`` is "not counted", never "zero" — a session that has
    genuinely had no turn reports ``0``.

    Both scopes report the SAME number from the same adapter projection: the
    project listing takes it from the parse it was already doing, and the
    host-wide catalog looks it up in the durable ``TurnCountCache``, which pays
    that parse once per transcript version on a background pass. So ``None`` at
    host scope means "not counted YET" (a cold cache, a transcript that just
    grew, or a row whose head read recovered no cwd to read it under), and it
    resolves to a number on a later fetch without the client doing anything.

    It counts HUMAN turns, so it can read one lower than the drill-in's
    ``SessionDetailView.total_turns`` for a resumed or compacted session, whose
    leading continuation block renders as a turn with no human prompt (measured:
    3 of 40 sampled on-host sessions, always by exactly 1). The alternative —
    ``len(read_turns)`` — costs a second parse over the sub-agent transcripts
    that the listing's main-thread parse never reads, per row, and a browse
    column does not earn that."""

    @classmethod
    def from_listing(cls, ls: SessionListing) -> SessionSummaryView:
        s = ls.summary
        return cls(
            session_id=s.session_id,
            adapter_kind=s.adapter_kind,
            provenance=ls.provenance,
            cwd=s.cwd,
            # The explicit, non-positional pin marker — a client could
            # infer this from `provenance == "grove_launched"` today, but that
            # field's job is "how was this session found", not "is this the
            # one Grove has pinned"; conflating the two forced every consumer
            # to duplicate the inference (the webapp's picker did, before
            # switching to the dashboard's positional `sessions[0]`). Derived
            # from the SAME `state.agent_session_id` equality `provenance`
            # already computes at this listing's one composition site
            # (`SessionExplorer._scan_workspace`) — no new engine state.
            primary=ls.provenance == "grove_launched",
            workspace_id=ls.workspace_id,
            workspace_title=ls.workspace_title,
            workspace_branch=ls.workspace_branch,
            git_branch=s.git_branch,
            created_at=s.created_at,
            modified_at=s.modified_at,
            size_bytes=s.size_bytes,
            title=s.title,
            first_prompt=s.first_prompt,
            last_prompt=s.last_prompt,
            activity=AgentActivityView.from_activity(s.activity),
            # Free: the listing's one parse already produced it. Read off the
            # engine dataclass rather than the view built beside it, so the
            # two fields cannot drift through a `from_activity` change.
            turn_count=s.activity.human_turns,
            # NOT free, unlike `turn_count`: the two clocks come from the
            # message spine (main + every sub-agent file), which the listing's
            # summary parse does not cover. The explorer measures it at this
            # scope rather than reading the catalog's cache, so the column is
            # filled for a user who never opens the host-wide list; it is the
            # same `duration_of` the cache stores, so the scopes agree.
            duration=ls.duration,
        )

    @classmethod
    def from_catalog(cls, e: CatalogEntry) -> SessionSummaryView:
        """One host-wide catalog row (``SessionCatalog.scan``).

        Everything here comes from the scan's bounded head read — no transcript
        is parsed, so ``activity``/``title``/the prompts stay null (see the
        class docstring). ``mtime`` is ``0.0`` exactly when the stat failed,
        which is a missing timestamp, not 1970.

        ``turn_count``, ``duration`` and ``size_bytes`` are the three fields
        that survive that rule. ``turn_count`` and ``duration`` because
        neither is measured here: both are looked up in the durable
        ``TurnCountCache``, which pays a full adapter parse ONCE per
        transcript version on a background worker and
        keys the result on the ``(mtime, size)`` the scan's own stat already
        implies. A session the cache has not reached yet — or whose
        transcript has changed since — is still ``None`` on both, which is why
        the fields stay nullable and why a client must keep reading them as
        *not measured*, never *zero*. ``size_bytes`` because it never needed a
        parse to begin with — it is read straight off ``ref.size_bytes``,
        which every filesystem adapter already populated from the same
        ``stat()`` call that produced ``mtime`` (see ``SessionRef``); it
        stays nullable for a remote-backed session or a stat that failed.
        """
        ref = e.ref
        return cls(
            session_id=ref.session_id,
            adapter_kind=ref.adapter_kind,
            provenance=e.provenance,
            primary=e.provenance == "grove_launched",
            workspace_id=e.workspace_id,
            workspace_title=e.workspace_title,
            workspace_branch=None,
            git_branch=ref.git_branch,
            created_at=ref.birth,
            modified_at=datetime.fromtimestamp(ref.mtime, UTC) if ref.mtime else None,
            size_bytes=ref.size_bytes,
            title=None,
            first_prompt=None,
            last_prompt=None,
            activity=None,
            turn_count=e.turn_count,
            # The catalog's other cached fact, from the same parse and the same
            # `(mtime, size)` entry as `turn_count` — so a row can never report
            # one without the other, and neither can be stale.
            duration=e.duration,
            cwd=ref.cwd,
            project=(SessionProjectView.from_project(e.project) if e.project is not None else None),
            live=e.live,
        )


class QueuedMessageView(BaseModel):
    """One message the HARNESS is holding until it can inject it.

    Both shipped harnesses queue a message typed while the agent is busy and
    decide themselves when to deliver it. Grove does not own that queue and must
    not keep a second one: it is the harness that drains it, and a Grove-side
    ledger would be a second writer with no arbitration — it would drift the
    moment somebody typed straight into the pane.

    So every field here is READ from the harness's own record. A queue Grove
    cannot observe is an empty tuple on :class:`WorkspaceQueueView`, never a
    reconstruction from what Grove happens to have sent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    """The message as the user wrote it, capped at the shared entry cap — a
    queued prompt is a chat line, not a diff."""

    sent_at: datetime | None = None
    """When it was SUBMITTED, which is not when it will be delivered. Null when
    the harness recorded no instant for it."""

    position: int = 0
    """Place in the queue, 0 first. The harness decides the order and may not
    honour it; this is what it currently reports, not a promise."""

    @classmethod
    def from_message(cls, m: QueuedMessage) -> QueuedMessageView:
        """Wire mirror of ``grove.core.agents.QueuedMessage``.

        The cap this class's ``text`` promises is applied HERE rather than at
        the route, so every producer of this shape honours it — a cap enforced
        at one call site is a cap the second call site does not have.
        """
        return cls(
            text=_truncate(m.text, _ENTRY_TEXT_CAP),
            sent_at=m.sent_at,
            position=m.position,
        )


class WorkspaceQueueView(BaseModel):
    """What a workspace's agent has queued but not yet acted on.

    Fetch-on-demand (``GET /workspaces/{id}/queue``) for the same reason turns
    are: the payload is unbounded in principle and the ~1 Hz stream must stay
    small. The live tick carries only a COUNT.

    An empty ``messages`` with ``supported=False`` is the honest answer for a
    harness whose queue Grove cannot see, and it is deliberately distinct from
    an empty queue on a harness it can — one is "nothing waiting", the other is
    "no idea", and a client renders them differently or lies.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[QueuedMessageView, ...] = ()
    supported: bool = True


class SessionControlView(BaseModel):
    """Wire mirror of ``grove.core.agents.SessionControl`` — one invokable session
    control (a slash command, a skill, or a configured MCP server).

    ``name`` is exactly what a trigger delivers (``POST .../controls/invoke``);
    ``scope`` is a display-only origin hint; ``detail`` an optional one-line
    description read off disk.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    scope: Literal["project", "user", "builtin", "dynamic"]
    detail: str | None = None

    @classmethod
    def from_control(cls, c: SessionControl) -> SessionControlView:
        return cls(name=c.name, scope=c.scope, detail=c.detail)


class SessionControlsView(BaseModel):
    """Wire mirror of ``grove.core.agents.SessionControls`` — the input
    controls a session exposes, fetch-on-demand (``GET /workspaces/{id}/controls``)
    and never on the SSE stream, exactly like the session-history reads above.

    Read-only display is the core value: the enumerated commands / skills /
    MCP servers / model catalog / current model / permission posture. The trigger
    verbs (``.../controls/invoke``, ``.../controls/model``) act on the ``name``s
    and model ids here. Every field degrades to empty/``None`` — an agent with no
    control surface (a shell, a remote session) yields an empty view, and the
    webapp renders no chrome for it.
    """

    model_config = ConfigDict(frozen=True)

    commands: list[SessionControlView]
    skills: list[SessionControlView]
    mcp_servers: list[SessionControlView]
    models: list[str]
    current_model: str | None
    permission_mode: str | None

    @classmethod
    def from_controls(cls, c: SessionControls) -> SessionControlsView:
        return cls(
            commands=[SessionControlView.from_control(x) for x in c.commands],
            skills=[SessionControlView.from_control(x) for x in c.skills],
            mcp_servers=[SessionControlView.from_control(x) for x in c.mcp_servers],
            models=list(c.models),
            current_model=c.current_model,
            permission_mode=c.permission_mode,
        )


class SessionDetailView(BaseModel):
    """One session with its conversation — the turns endpoint's response.

    ``turns`` is a WINDOW, not necessarily the whole session, and the three
    fields below are what make it self-describing: a client splices by reading
    them, never by remembering what it asked for.

    A turn's identity is its ORDINAL in a forward walk of an append-only
    transcript, which is why a plain integer is a sufficient cursor — appending
    a record extends the last turn, and a new human prompt starts turn N+1, so
    an earlier turn can never be renumbered. Measured on a live session: over
    45 s of an agent working, 6 of 7 turns stayed byte-identical and only the
    tail moved.
    """

    model_config = ConfigDict(frozen=True)

    session: SessionSummaryView
    turns: list[SessionTurnView]

    total_turns: int = 0
    """How many turns the session holds right now — the ceiling of the ordinal
    space, regardless of how many this response carries."""

    first_turn_index: int = 0
    """The ordinal of ``turns[0]`` within the session. ``0`` for a whole read;
    a windowed read (``after_turn=`` or ``last=``) reports where its window
    starts so the client can splice without inferring anything."""

    incremental: bool = False
    """True only when the server HONOURED an ``after_turn`` cursor.

    False is the gap signal, and it is deliberately fail-safe: any condition
    the daemon cannot prove it can serve incrementally (a cursor beyond the
    end, i.e. a transcript replaced under the reader; or no cursor at all)
    answers with the whole session and False, and the client REPLACES rather
    than appends. This is the SSE replay contract applied to a fetch —
    ``_SseHub.can_replay`` likewise falls back to a full snapshot rather than a
    partial one, because a cheap transcript that silently drops a turn is worse
    than an expensive one.

    An empty ``turns`` with True is the ordinary "nothing new since your
    cursor" answer, not a gap.
    """

    @classmethod
    def from_listing_turns(
        cls,
        listing: SessionListing,
        turns: tuple[SessionTurn, ...],
        *,
        total_turns: int | None = None,
        first_turn_index: int = 0,
        incremental: bool = False,
    ) -> SessionDetailView:
        # The session's cwd anchors file-edit paths to a worktree-relative
        # display form; it stays engine-side and never crosses the wire itself.
        anchor = listing.summary.cwd
        turn_views = cls._drop_superseded_todos(
            [SessionTurnView.from_turn(t, anchor) for t in turns]
        )
        return cls(
            session=SessionSummaryView.from_listing(listing),
            turns=turn_views,
            total_turns=len(turns) if total_turns is None else total_turns,
            first_turn_index=first_turn_index,
            incremental=incremental,
        )

    @classmethod
    def from_catalog_turns(
        cls,
        entry: CatalogEntry,
        turns: tuple[SessionTurn, ...],
        *,
        total_turns: int | None = None,
        first_turn_index: int = 0,
    ) -> SessionDetailView:
        """The same response for a session that belongs to no workspace — the
        catalog's drill-in. Identical anchoring, sourced from the row's own
        ``cwd`` instead of a listing's.

        No ``incremental`` parameter: this route browses history rather than
        following a live agent, so it never serves a cursor and the field is
        always False.
        """
        turn_views = cls._drop_superseded_todos(
            [SessionTurnView.from_turn(t, entry.ref.cwd) for t in turns]
        )
        return cls(
            session=SessionSummaryView.from_catalog(entry),
            turns=turn_views,
            total_turns=len(turns) if total_turns is None else total_turns,
            first_turn_index=first_turn_index,
        )

    @staticmethod
    def _drop_superseded_todos(turns: list[SessionTurnView]) -> list[SessionTurnView]:
        """Null every ``role=="todo"`` entry's structured payload except the
        NEWEST one in this window — a todo write is a FULL REWRITE of the
        list, never a diff, so of every board this window carries only the
        last one is current and every earlier one is pure waste on the wire.
        Measured on a real 40-turn window: 119 todo entries, 1,100,957 bytes
        — 28.9% of a 3,810,728-byte response — all but one of them stale
        (see the daemon CLAUDE.md's ``/turns`` section for the full figures).

        **Lossless, not a cap — deliberately carries no ``*_truncated``
        signal.** Every other bound in this module (``_TOOL_BODY_CAP``, the
        file-edit ceiling) trims a single fact that is still wanted whole,
        so the trim needs a flag saying a byte range was withheld. A
        superseded board is not that: it is fully and unconditionally
        replaced by the next ``TodoWrite``, so an older entry contributes
        nothing a client could ever act on once a newer one exists in the
        SAME response — there is no "restore this" a truncation flag would
        promise. It mirrors ``CompactionView``'s own digest projection
        (payload nulled, role and text kept) rather than inventing a new
        idiom, and the client that wants the CURRENT board in full already
        has the honest source for it: ``GET /workspaces/{id}/todo`` (the
        todo axis is pull-only — see ``grove.core``'s manager docs for why).

        **The ENTRY survives — only ``.todo`` is nulled.** ``role``/``text``
        (``TodoList.summary``, a one-line progress digest) stay, so this
        never shifts a later entry's position or an entry count a client
        might render — only the whole-list payload, which is the expensive
        part, disappears. That is also what keeps an old client's behaviour
        unchanged: ``todo: TodoListView | None`` was already nullable, so
        this is a runtime-only change with no wire-schema diff.

        **"Newest" is purely POSITIONAL — never filtered by content —
        because a CLEARED plan is real information.** An agent that empties
        its board (every item done, or the plan abandoned) writes an empty
        list; picking "the newest non-empty board" instead would silently
        resurrect a stale, already-superseded list right after the agent
        explicitly cleared it. Selecting strictly by position (turn index,
        then entry index) treats an empty final board as what it is: the
        current state.
        """
        newest_turn = -1
        newest_entry = -1
        for ti, turn in enumerate(turns):
            for ei, entry in enumerate(turn.entries):
                if entry.role == "todo":
                    newest_turn, newest_entry = ti, ei
        if newest_turn < 0:
            return turns  # No todo entries in this window — nothing to project.

        projected: list[SessionTurnView] = []
        for ti, turn in enumerate(turns):
            if not any(e.role == "todo" for e in turn.entries):
                projected.append(turn)
                continue
            entries = [
                entry
                if entry.role != "todo" or (ti, ei) == (newest_turn, newest_entry)
                else entry.model_copy(update={"todo": None})
                for ei, entry in enumerate(turn.entries)
            ]
            projected.append(turn.model_copy(update={"entries": entries}))
        return projected
