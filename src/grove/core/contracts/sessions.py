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
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.contracts.activity import AgentActivityView
from grove.core.contracts.questions import (
    _ENTRY_TEXT_CAP,
    AgentQuestionView,
    _truncate,
)

if TYPE_CHECKING:
    from grove.core.agents import (
        DigestEntry,
        FileEdit,
        SessionControl,
        SessionControls,
        SessionTurn,
        TodoList,
    )
    from grove.core.sessions import CatalogEntry, ProjectContext, SessionListing

# A diff is legitimately bigger than a chat line — a generous ceiling well
# above ``_ENTRY_TEXT_CAP`` (4000) so an ordinary file edit never truncates,
# while still bounding the pathological case (a multi-MB generated file).
_FILE_EDIT_TEXT_CAP = 100_000

# Mirrors ``grove.core.agents.TodoStatus``. Duplicated (not imported) so the
# contracts package never drags the engine in at runtime — the webapp codegen
# drift-check is the guard, exactly like ``AgentQuestionKind`` in ``questions.py``.
TodoStatus = Literal["pending", "in_progress", "completed"]


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


class DigestEntryView(BaseModel):
    """Wire mirror of ``grove.core.agents.DigestEntry`` (text capped).

    ``question``/``file_edit``/``todo`` are set only for their matching role
    (``"question"``/``"file_edit"``/``"todo"``) — the structured payload; every
    other role leaves all three ``None`` and reads from ``text``.
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
    ]
    text: str
    question: AgentQuestionView | None = None
    file_edit: FileEditView | None = None
    todo: TodoListView | None = None

    @classmethod
    def from_entry(cls, e: DigestEntry, anchor: str | None = None) -> DigestEntryView:
        return cls(
            role=e.role,
            text=_truncate(e.text, _ENTRY_TEXT_CAP),
            question=AgentQuestionView.from_question(e.question) if e.question else None,
            file_edit=FileEditView.from_edit(e.file_edit, anchor) if e.file_edit else None,
            todo=TodoListView.from_todo(e.todo) if e.todo else None,
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

    @classmethod
    def from_turn(cls, t: SessionTurn, anchor: str | None = None) -> SessionTurnView:
        return cls(
            user_text=_truncate(t.user_text, _ENTRY_TEXT_CAP),
            started_at=t.started_at,
            entries=[DigestEntryView.from_entry(e, anchor) for e in t.entries],
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
    ``size_bytes`` and ``activity`` come from a full transcript parse the
    host-wide scan deliberately never pays (it is metadata-only, one bounded
    head read per session), so a catalog row leaves both null. Read them as
    "not parsed at this scope", never as "zero".

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
        )

    @classmethod
    def from_catalog(cls, e: CatalogEntry) -> SessionSummaryView:
        """One host-wide catalog row (``SessionCatalog.scan``).

        Everything here comes from the scan's bounded head read — no transcript
        is parsed, so ``size_bytes``/``activity``/``title``/the prompts stay
        null (see the class docstring). ``mtime`` is ``0.0`` exactly when the
        stat failed, which is a missing timestamp, not 1970.
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
            size_bytes=None,
            title=None,
            first_prompt=None,
            last_prompt=None,
            activity=None,
            cwd=ref.cwd,
            project=(SessionProjectView.from_project(e.project) if e.project is not None else None),
            live=e.live,
        )


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
    """One session with its conversation — the turns endpoint's response."""

    model_config = ConfigDict(frozen=True)

    session: SessionSummaryView
    turns: list[SessionTurnView]

    @classmethod
    def from_listing_turns(
        cls, listing: SessionListing, turns: tuple[SessionTurn, ...]
    ) -> SessionDetailView:
        # The session's cwd anchors file-edit paths to a worktree-relative
        # display form; it stays engine-side and never crosses the wire itself.
        anchor = listing.summary.cwd
        return cls(
            session=SessionSummaryView.from_listing(listing),
            turns=[SessionTurnView.from_turn(t, anchor) for t in turns],
        )

    @classmethod
    def from_catalog_turns(
        cls, entry: CatalogEntry, turns: tuple[SessionTurn, ...]
    ) -> SessionDetailView:
        """The same response for a session that belongs to no workspace — the
        catalog's drill-in. Identical anchoring, sourced from the row's own
        ``cwd`` instead of a listing's."""
        return cls(
            session=SessionSummaryView.from_catalog(entry),
            turns=[SessionTurnView.from_turn(t, entry.ref.cwd) for t in turns],
        )
