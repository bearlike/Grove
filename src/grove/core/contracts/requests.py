"""Request envelopes — every client constructs one and sends it to the engine.

The shape every Grove client (TUI, future API server, future web
client) speaks. A single Pydantic boundary; the engine never accepts
loose kwargs from outside. ``model_config`` is ``extra='forbid'`` so a
client that drifts on field names fails loudly with a 422-shaped error
rather than quietly missing a field.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from grove.core.contracts.branch_plan import AutoBranch, BranchPlan


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

    branch_plan: BranchPlan = Field(default_factory=AutoBranch)
    """How the workspace's branch and placement are sourced. See
    ``grove.core.contracts.branch_plan`` for the five variants — four produce a
    worktree, ``RootBranch`` runs in the repo root."""

    skip_init: bool = False
    """Skip the init script for this create only, regardless of
    ``init_script.enabled``. A per-create override of a config default
    (mechanism, not policy): the init script is built for a fresh worktree, so
    it can be unwanted or unsafe in the repo root, and some worktrees simply
    don't need it. Records ``InitStatus.SKIPPED``. Does not persist — it is a
    create-time decision, never re-applied on resume/respawn."""

    repo_root: Path | None = None
    """Repository root for the workspace. ``None`` for in-process callers
    (the TUI knows its own repo). The HTTP daemon requires this set so it
    can dispatch to the right ``WorkspaceManager`` — its handler returns
    422 when missing."""


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

    @model_validator(mode="after")
    def _at_least_one_field(self) -> UpdateWorkspaceRequest:
        if self.title is None and self.description is None:
            raise ValueError("provide at least one of title, description")
        return self


__all__ = ["CreateWorkspaceRequest", "UpdateWorkspaceRequest"]
