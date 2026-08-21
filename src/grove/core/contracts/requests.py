"""Request envelopes — every client constructs one and sends it to the engine.

The shape every Grove client (TUI, future API server, future web
client) speaks. A single Pydantic boundary; the engine never accepts
loose kwargs from outside. ``model_config`` is ``extra='forbid'`` so a
client that drifts on field names fails loudly with a 422-shaped error
rather than quietly missing a field.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from grove.core.contracts.branch_plan import AutoBranch, BranchPlan
from grove.core.contracts.tickets import TicketSelector
from grove.core.workspace import Runtime

_MODEL_ID_MAX_LENGTH = 64
_MODEL_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:\[\]-]*\Z")


class CreateWorkspaceRequest(BaseModel):
    """Payload for ``WorkspaceManager.create()``.

    ``branch_plan`` defaults to ``AutoBranch()`` so callers that don't
    care about branch semantics get the historical Grove behavior for
    free — Grove generates ``{prefix}{slug(title)}-{ts}`` off ``HEAD``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    agent_name: str = Field(min_length=1)
    """The agent to spawn in the workspace's tmux ``agent`` window. Must
    match an entry in the merged ``cfg.agents`` list at create time."""

    title: str = Field(min_length=1, max_length=120)
    """Human-readable workspace label. Drives the slug used by the worktree
    path and the tmux session name. Independent of the branch — the
    branch name comes from ``branch_plan``."""

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
        if value is None:
            return None
        model = value.strip()
        if not model:
            return None
        if len(model) > _MODEL_ID_MAX_LENGTH:
            raise ValueError(f"model id {model!r} exceeds {_MODEL_ID_MAX_LENGTH} characters")
        # This limits argv shape, not model semantics: providers still validate any id we forward.
        # A separator is admitted unless dangerous, not excluded unless proven necessary.
        # Brackets occur in real gateway ids; list-form argv with shell=False never expands them.
        if not _MODEL_ID_PATTERN.fullmatch(model):
            raise ValueError(f"invalid model id {model!r}")
        return model

    branch_plan: BranchPlan = Field(default_factory=AutoBranch)
    """How the workspace's branch and placement are sourced. See
    ``grove.core.contracts.branch_plan`` for the five variants — four produce a
    worktree, ``RootBranch`` runs in the repo root."""

    runtime: Runtime | None = None
    """Where this workspace's agent runs: ``"container"`` (the default) or
    ``"host"``. ``None`` — the default — takes the cascade's answer
    (``container.enabled``), so a caller that does not care never has to know
    the field exists.

    Explicit ``"host"`` is the escape hatch, and it is a *recorded choice*, not
    a fallback: it is never warned about and a later ``respawn`` never
    auto-upgrades it. It is refused outright when the project's committed
    devcontainer config declares ``customizations.grove.requires_container``.

    Persisted (unlike ``skip_init`` / ``model``): the answer selects a launch
    backend and every lifecycle verb needs it, so it lives on the record rather
    than being re-derived from a config default that may later flip."""

    brief: bool | None = None
    """Hand this workspace's agent Grove's first-turn brief — one short note
    pointing it at the ``working-in-grove`` skill. ``None`` — the default —
    takes the cascade's answer (``brief.enabled``), exactly like ``runtime``.

    Persisted for the same reason ``runtime`` is: the delivery happens at every
    launch, so a workspace created while the default was on must keep being
    briefed after somebody flips the default off, and vice versa."""

    skip_init: bool = False
    """Skip the init script for this create only, regardless of
    ``init_script.enabled``. A per-create override of a config default
    (mechanism, not policy): the init script is built for a fresh worktree, so
    it can be unwanted or unsafe in the repo root, and some worktrees simply
    don't need it. Records ``InitStatus.SKIPPED``. Does not persist — it is a
    create-time decision, never re-applied on resume/respawn."""

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

    repo_root: Path | None = None
    """Repository root for the workspace. ``None`` for in-process callers
    (the TUI knows its own repo). The HTTP daemon requires this set so it
    can dispatch to the right ``WorkspaceManager`` — its handler returns
    422 when missing."""

    project_cwd: Path | None = None
    """Absolute path the agent session should start in — a nested *project*
    directory inside the repo. ``None`` (the default) starts the agent at
    the worktree root, the historical behavior. When set it must be the repo
    root or a subdirectory of it; the engine derives the subpath relative to the
    repo root and starts the agent in the matching subdir of the worktree. The
    git worktree and branch are **always** anchored at the repo root regardless
    — this field separates "where the agent works" from "where the worktree
    lives", letting several subdirs of one repo be distinct projects."""


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
