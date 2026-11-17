"""Request envelopes — every client constructs one and sends it to the engine.

The shape every Grove client (TUI, future API server, future web
client) speaks. A single Pydantic boundary; the engine never accepts
loose kwargs from outside. ``model_config`` is ``extra='forbid'`` so a
client that drifts on field names fails loudly with a 422-shaped error
rather than quietly missing a field.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grove.core.config import AgentKind, validate_model_id
from grove.core.contracts.attachments import AttachmentUploadRequest
from grove.core.contracts.branch_plan import BranchPlan
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.contracts.tickets import TicketSelector
from grove.core.workspace import Runtime, WorkspaceTitle


class CreateWorkspaceRequest(BaseModel):
    """Payload for ``WorkspaceManager.create()``.

    Every launch field may be omitted, and omitted means "resolve it": the
    engine's ``LaunchResolver`` answers it from the request, then the project's
    saved defaults, then the user's, then Grove's built-in (the first agent, on
    the host, in the current checkout). A client sends only what the person
    chose, so a pre-filled form never pins today's config onto the workspace.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    agent_name: str | None = Field(default=None, min_length=1)
    """The agent to spawn in the workspace's tmux ``agent`` window. Must
    match an entry in the merged ``cfg.agents`` list at create time. ``None``
    takes the saved ``defaults.agent``, else the first configured agent."""

    agent_kind: AgentKind | None = None
    """The tool to run when the caller chose a tool but not its profile. The engine
    picks that tool's profile for the resolved runtime (``defaults.profiles``).
    Ignored when ``agent_name`` is set."""

    title: str = Field(default_factory=WorkspaceTitle.generate, min_length=1, max_length=120)
    """Human-readable workspace label. Drives the slug used by the worktree
    path and the tmux session name. Independent of the branch — the
    branch name comes from ``branch_plan``.

    Omit it and the engine names the workspace ``wk-`` plus six random hex
    characters, never anything derived from the prompt. Every client relies on
    this one rule, and the agent is asked to replace the name once it knows the
    task."""

    description: str | None = Field(default=None, max_length=2000)
    """Optional free-form text the user attaches to the workspace.
    Persisted as-is on the resulting ``WorkspaceState``. Empty string is
    treated equivalent to ``None`` by the engine; no separate "cleared"
    state on the wire."""

    model: str | None = Field(default=None)
    """Optional model id for this create only, forwarded to the agent tool as its
    model argument at launch (``claude --model <id>`` / ``codex --model <id>``).
    ``None`` (the default) lets the tool pick its own default — Grove never
    second-guesses the model, it only forwards the parameter (the provider
    boundary). Kinds with no launch-time model flag (mewbo, generic) ignore it.
    Create-time only, like ``skip_init`` — never persisted or re-applied."""

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str | None) -> str | None:
        # The rule lives beside `WorkspaceDefaults.model`, which the engine now
        # resolves into this same argv position — one definition, or the saved
        # default is admitted under a looser rule than the wire's.
        return validate_model_id(value)

    effort: str | None = Field(default=None, pattern=r"^[a-z]+$", max_length=16)
    """Optional reasoning effort for this create only, forwarded as the agent's own
    flag (``claude --effort <level>`` / ``codex -c model_reasoning_effort=<level>``).
    ``None`` (the default) leaves the tool's own default in place. The level must be
    one ``AgentSummaryView.efforts`` lists for the agent; anything else is refused
    rather than dropped. The launch flag is create-time only, like ``model``; a
    running native session changes it through ``POST .../controls/effort``."""

    branch_plan: BranchPlan | None = None
    """How the workspace's branch and placement are sourced. See
    ``grove.core.contracts.branch_plan`` for the five variants — four produce a
    worktree, ``RootBranch`` runs in the repo root. ``None`` takes the saved
    ``defaults.branch_mode``, else ``RootBranch``: the current checkout, with no
    new branch or worktree."""

    runtime: Runtime | None = None
    """Where this workspace's agent runs: ``"container"``, ``"host"`` or ``"remote"``.
    ``None`` — the default — takes the saved ``defaults.runtime``, else the
    host. Configuring containers makes them available; it never selects them.

    Explicit ``"host"`` is the escape hatch, and it is a *recorded choice*, not
    a fallback: it is never warned about and a later ``respawn`` never
    auto-upgrades it. It is refused outright when the project's committed
    devcontainer config declares ``customizations.grove.requires_container``.

    Persisted (unlike ``skip_init`` / ``model``): the answer selects a launch
    backend and every lifecycle verb needs it, so it lives on the record rather
    than being re-derived from a config default that may later flip."""

    remote_runtime: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+$", max_length=64)
    """Which ``runtimes.<name>`` block runs a ``"remote"`` workspace. ``None``
    takes the only resident block when exactly one is configured and is refused
    otherwise; ignored for any other ``runtime``."""

    brief: bool | None = None
    """Hand this workspace's agent Grove's first-turn brief — one short note
    pointing it at the ``working-in-grove`` skill. ``None`` — the default —
    takes the cascade's answer (``GroveConfig.default_brief``), exactly like
    ``runtime``.

    Persisted for the same reason ``runtime`` is: the delivery happens at every
    launch, so a workspace created while the default was on must keep being
    briefed after somebody flips the default off, and vice versa."""

    native: bool | None = None
    """Run this workspace's agent as a Grove-owned native session (Claude Code
    stream-json / Codex app-server) rather than its interactive terminal.
    ``None`` — the default — takes the roster entry's own ``AgentSpec.native``,
    exactly like ``runtime`` takes the cascade's. ``True`` on an agent kind
    with no native protocol (``generic``/``mewbo``) is ignored: the kind, not
    the request, decides whether a control channel exists to own.

    Persisted for the same reason ``runtime`` is: every launch and every steer
    verb reads the record to know whether a pane or a control channel is
    there, so a later roster edit must not re-decide for a running workspace."""

    skip_init: bool | None = None
    """Skip the init script for this create only, regardless of
    ``init_script.enabled``. A per-create override of a config default
    (mechanism, not policy): the init script is built for a fresh worktree, so
    it can be unwanted or unsafe in the repo root, and some worktrees simply
    don't need it. Records ``InitStatus.SKIPPED``. Does not persist — it is a
    create-time decision, never re-applied on resume/respawn.

    ``None`` rather than ``False`` because a bool cannot say "unspecified", and
    without that a saved ``defaults.skip_init`` was unreachable: every omitted
    field arrived as an explicit ``False`` the engine could not tell from a
    caller that meant it. ``False`` still parses and still means "run it", so no
    existing client changes."""

    ticket: TicketSelector | None = None
    """Optional ticket to associate at create. When set alongside an
    ``AutoBranch`` plan, the generated branch becomes ticket-aware
    (``{branch_prefix}{provider-formatted key + slug}``), so the tracker links
    PRs/commits automatically. The provider must be enabled or create fails
    before any side effect. Regardless of this field, the *final* branch name is
    re-parsed through the providers to derive ``ticket_refs`` — so a non-auto
    branch that already carries a key is associated too."""

    initial_prompt: str | None = Field(default=None, max_length=10_000)
    """The agent's first task, delivered race-free as the session boots so the
    workspace starts *working* instead of idling at the prompt. Delivered
    through the launch invocation, never typed in post-boot (which races the
    agent's boot — the swallowed-Enter trap): claude_code appends it as a
    trailing positional arg to the launch argv (``claude … "<prompt>"`` starts
    already working on it); mewbo re-engages the freshly-created session via its
    ``/message`` API (no boot race). A bare shell (generic) has no prompt concept
    and ignores it. Create-only — never re-applied on resume/respawn, like
    ``skip_init``."""

    attachments: list[AttachmentUploadRequest] = Field(default_factory=list, max_length=20)
    """Files to store in the new workspace and name in ``initial_prompt``.

    **They ride the CREATE rather than a follow-up upload, and the reason is the
    race-free delivery above.** A workspace composer can post to
    ``/workspaces/{id}/attachments`` because its workspace already exists; the
    landing composer's whole action is *"here is a prompt, make me a workspace"*,
    so there is no id to upload against until the thing being described has been
    built. Creating first and steering afterwards would trade the launch-argv
    delivery for a post-boot type, which is the boot race ``initial_prompt``
    exists to avoid — and would leave a partial failure as a live workspace whose
    prompt names files that never arrived.

    The engine stores them once the worktree exists and appends the same
    Grove-fenced block ``send_message`` uses, so a create and a follow-up message
    put an agent in front of identical text. Capped at 20 because this is one
    request body; the per-file ceiling is ``AttachmentStore.MAX_BYTES``."""

    resume_session_id: str | None = Field(default=None, max_length=200)
    """Adopt an EXISTING agent session as this workspace's primary instead of
    minting a fresh one. ``None`` (the default) mints as usual. When set,
    the id is persisted as ``agent_session_id`` (so the dashboard tracks the
    resumed session by construction — no discovery needed) and the agent is
    launched to CONTINUE it: ``claude --resume <id>`` (which keeps the same
    session id/file) or ``codex resume <id>``. Only ``claude_code`` and ``codex``
    agents can resume by id — a mewbo/generic agent rejects with a clear error
    (``ResumeNotSupported``, 422) before any side effect. Create-only, never
    re-applied on resume/respawn, like ``skip_init``. Accepts a full id OR a
    unique id prefix scoped to the project (resolved through the same
    ``SessionExplorer`` the remap verb uses); an unknown/ambiguous ref, or
    one whose adapter kind mismatches the agent, fails with
    ``AgentSessionNotFound`` (404) before any side effect — never a
    fully-provisioned workspace stranded on a bogus id."""

    fork_session: bool | None = None
    """Start from a COPY of ``resume_session_id`` instead of continuing it.
    ``None`` and ``False`` both continue it, and the published schema keeps the
    field optional, like every other launch flag here.

    The source session is left untouched and keeps running; the new workspace
    gets its own session id (``claude --resume <id> --fork-session``, ``codex
    fork <id>`` or Codex ``thread/fork``). Unlike a plain resume this works for a
    native session too. An agent whose tool cannot fork (OpenCode, mewbo
    or generic) is refused with ``ResumeNotSupported`` (422) before any side
    effect. Requires ``resume_session_id``. Create-only, never persisted."""

    @model_validator(mode="after")
    def _fork_needs_source(self) -> CreateWorkspaceRequest:
        """Refuse a fork that names no session to copy.

        Returns:
            The request, unchanged.

        Raises:
            ValueError: ``fork_session`` is set without ``resume_session_id``.
        """
        if self.fork_session and self.resume_session_id is None:
            raise ValueError("fork_session requires resume_session_id")
        return self

    repo_root: Path | None = None
    """Repository root for the workspace. ``None`` for in-process callers
    (the TUI knows its own repo). The HTTP daemon requires this set so it
    can dispatch to the right ``WorkspaceManager`` — its handler returns
    422 when missing."""

    project: ProjectIdentity | None = None
    """The project by its cross-node name instead of a path. A daemon resolves it
    to its own path for that remote and ignores it when ``repo_root`` is set;
    this is how a hub names a project to a node that has never seen the hub's
    paths. A node with no such repository refuses with ``project_not_on_node``."""

    selector: dict[str, str] | None = None
    """Node labels the new workspace requires on a federation hub, for example
    ``{"residency": "eu"}``. Unset uses the project's ``scheduling.require``.
    Ignored by a daemon that is not a hub."""

    node: str | None = Field(default=None, min_length=1, max_length=128)
    """The connected node a federation hub must place this create on, by name.
    Unset lets the hub pick among the nodes that fit. Ignored by a daemon that
    is not a hub."""

    project_cwd: Path | None = None
    """Absolute path the agent session should start in — a nested *project*
    directory inside the repo. ``None`` (the default) starts the agent at
    the worktree root, the historical behavior. When set it must be the repo
    root or a subdirectory of it; the engine derives the subpath relative to the
    repo root and starts the agent in the matching subdir of the worktree. The
    git worktree and branch are **always** anchored at the repo root regardless
    — this field separates "where the agent works" from "where the worktree
    lives", letting several subdirs of one repo be distinct projects."""

    @staticmethod
    def runtime_fields(value: str | None) -> dict[str, Runtime | str]:
        """The ``runtime``/``remote_runtime`` pair one user-facing runtime name means.

        ``host`` and ``container`` name a runtime; any other value names a
        resident ``runtimes.<name>`` block and implies ``remote``. Whether that
        block exists is the engine's refusal, so a shell never guesses. ``None``
        leaves both unset for the cascade.
        """
        if value is None:
            return {}
        if value in (Runtime.HOST, Runtime.CONTAINER):
            return {"runtime": Runtime(value)}
        return {"runtime": Runtime.REMOTE, "remote_runtime": value}


class UpdateWorkspaceRequest(BaseModel):
    """Payload for ``WorkspaceManager.update()`` — partial metadata edit.

    Wire semantics: ``null`` (or field omitted) means "do not change".
    To clear an existing description, send ``""``. Title cannot be
    cleared — workspaces are required to have a non-empty title at all
    times.

    The model validator refuses an entirely-empty body so callers can't
    issue a no-op PATCH that bumps ``updated_at`` for free; the engine
    has its own no-op short-circuit for "values match current", but this
    catches the obvious "forgot to set anything" client bug at the
    request boundary.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    title: str | None = Field(default=None, min_length=1, max_length=120)
    """New title. ``None`` / omitted leaves the title unchanged. Must be
    1..120 characters when present."""

    description: str | None = Field(default=None, max_length=2000)
    """New description. ``None`` / omitted leaves the description
    unchanged. Empty string clears the description. ``max_length`` is
    a soft cap that mirrors the engine's validation — clients should
    truncate for the textarea, the engine is the source of truth."""

    share: bool | None = None
    """Whether this workspace is publicly readable. ``None`` / omitted leaves
    sharing exactly as it is — which is what makes an ordinary rename safe:
    a client PATCHing a title must never turn sharing off by not mentioning it.

    ``true`` mints a public link (idempotent — an already-shared workspace keeps
    the token it has, so re-enabling never breaks a link somebody is holding).
    ``false`` revokes, permanently: the token is cleared rather than parked, and
    re-sharing later mints a fresh one. The token itself comes back on
    ``WorkspaceStateView.share_token``; it is never accepted as input, because
    the engine is the only thing that may decide what a capability is."""

    share_session_id: str | None = Field(default=None, min_length=1)
    """Which session transcript the public link shows. ``None`` / omitted keeps
    whatever the link is already pinned to.

    Minting a link pins it automatically, to the workspace's session at that
    moment, so the ordinary path never sends this. It exists to RE-PIN a link
    already in circulation: enabling is idempotent, so ``share: true`` alone
    cannot move a live link's transcript, and that silence is deliberate —
    clicking share twice must not quietly change what a URL somebody already
    holds renders.

    Requires ``share: true`` (a pin without a link is a claim about nothing) and
    accepts a unique id-prefix. An id this workspace's adapter could never read
    is rejected as ``agent_session_not_found`` rather than stored as a dead
    pointer that answers 200."""

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateWorkspaceRequest:
        if self.title is None and self.description is None and self.share is None:
            raise ValueError("provide at least one of title, description, share")
        return self

    @model_validator(mode="after")
    def _pin_requires_sharing(self) -> UpdateWorkspaceRequest:
        # Mirrored engine-side too — this one exists so the refusal lands as a
        # 422 naming the field rather than as a generic engine error.
        if self.share_session_id is not None and self.share is not True:
            raise ValueError("share_session_id requires share: true")
        return self


__all__ = ["CreateWorkspaceRequest", "UpdateWorkspaceRequest"]
