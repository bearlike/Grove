"""BranchPlan — discriminated union of branch-source variants.

The wire-level shape every Grove client (TUI today, web/API
tomorrow) constructs and submits to the engine via
`CreateWorkspaceRequest`. Pydantic v2 with a `kind: Literal[...]`
discriminator gives every client a JSON Schema for free, and
`extra="forbid"` catches typos at the boundary.

Each variant **owns its own resolution**: the `resolve(cfg, title, ts)`
method returns a `ResolvedBranch` — the internal IR `WorkspaceManager`
consumes. Free helpers like `make_branch_for_plan(plan, ...)` are
deliberately not provided; the variant is the natural home for "how do
I become a real git operation". This is the code-as-poem principle in
microcosm: state and the methods that operate on it live together.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.config import GroveConfig
from grove.core.workspace import BranchProvenance, slug

_FROZEN = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class BranchMode(StrEnum):
    """Whether the resolved branch should be created (`-b`) or checked out as-is."""

    NEW = "new"
    """`git worktree add -b <name> <path> <base>` — Grove creates a fresh local branch."""

    CHECKOUT = "checkout"
    """`git worktree add <path> <name>` — branch already exists; just check it out."""


@dataclass(frozen=True, slots=True)
class ResolvedBranch:
    """Intermediate representation produced by `BranchPlan.resolve()`.

    Pure data, internal to the engine — never serialized, never crosses a
    wire. Plain dataclass (not Pydantic) on purpose: at this layer Pydantic
    would be ceremony without payoff because no client ever constructs one.
    """

    name: str
    """The local branch name we'll end up checked out on."""

    base_ref: str | None
    """Git revision to base a new branch off of. `None` for CHECKOUT mode
    (the branch already exists, so there is no `-b` flag)."""

    mode: BranchMode

    provenance: BranchProvenance
    """Persisted onto `WorkspaceState.branch_provenance`. Drives kill default."""

    tracks: str | None = None
    """Remote ref to set as upstream after creating a new tracking branch
    (TrackRemoteBranch only). `None` for every other variant."""


# ─── variants ────────────────────────────────────────────────────────────────


class AutoBranch(BaseModel):
    """Grove generates ``{branch_prefix}{slug(title)}-{ts}`` off ``base_ref``.

    The default create behavior — the same shape Grove ships today, now
    explicit. ``base_ref`` accepts any git revision (branch, tag, sha,
    ``HEAD``, ``origin/main``); validation that it actually exists happens
    in the engine when ``create()`` runs, so this Pydantic shape stays
    repo-agnostic and serializable.
    """

    model_config = _FROZEN

    kind: Literal["auto"] = "auto"
    base_ref: str = Field(default="HEAD", min_length=1)

    def resolve(self, cfg: GroveConfig, title: str, ts: str) -> ResolvedBranch:
        return ResolvedBranch(
            name=f"{cfg.worktree.branch_prefix}{slug(title)}-{ts}",
            base_ref=self.base_ref,
            mode=BranchMode.NEW,
            provenance=BranchProvenance.GROVE_CREATED,
        )


class NewNamedBranch(BaseModel):
    """User-supplied branch name, off ``base_ref``.

    Grove still owns the worktree path and tmux session names (they follow
    ``slug(title)``); only the branch is the user's namespace. The pattern
    accepts alphanumerics, dot, dash, underscore, slash; rejects a leading
    dash so the value can never be parsed as a CLI flag downstream.
    """

    model_config = _FROZEN

    kind: Literal["new_named"] = "new_named"
    # First char: alnum / dot / underscore / slash. Subsequent: same plus dash.
    # The split avoids the leading-dash case (which would be parsed as a CLI
    # flag downstream) without needing a regex lookahead — Pydantic v2 uses
    # Rust regex by default and does not support `(?!-)`.
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._/][A-Za-z0-9._/\-]*$")
    base_ref: str = Field(default="HEAD", min_length=1)

    def resolve(self, cfg: GroveConfig, title: str, ts: str) -> ResolvedBranch:
        del cfg, title, ts  # name is user-supplied; nothing to derive
        return ResolvedBranch(
            name=self.name,
            base_ref=self.base_ref,
            mode=BranchMode.NEW,
            provenance=BranchProvenance.GROVE_CREATED,
        )


class ExistingLocalBranch(BaseModel):
    """Check out an existing local branch into a new worktree.

    No new branch is created. Provenance is ``USER_ATTACHED`` so kill
    defaults to keeping the branch — this is the user's pre-existing
    feature branch and a workspace tear-down must not lose it.
    """

    model_config = _FROZEN

    kind: Literal["existing_local"] = "existing_local"
    name: str = Field(min_length=1)

    def resolve(self, cfg: GroveConfig, title: str, ts: str) -> ResolvedBranch:
        del cfg, title, ts
        return ResolvedBranch(
            name=self.name,
            base_ref=None,
            mode=BranchMode.CHECKOUT,
            provenance=BranchProvenance.USER_ATTACHED,
        )


class TrackRemoteBranch(BaseModel):
    """Track a remote branch by creating a fresh local tracking branch.

    ``remote_ref`` is the full remote-qualified ref (e.g. ``origin/feature/x``).
    ``local_name`` defaults to the part after the first ``/`` — strip the
    remote name and use whatever's left. Provenance is ``GROVE_CREATED``
    because the local branch is fresh: if the user later kills the
    workspace and the local branch goes with it, the remote ref still
    has every commit (and they can re-track at any time).
    """

    model_config = _FROZEN

    kind: Literal["track_remote"] = "track_remote"
    remote_ref: str = Field(min_length=3, pattern=r"^[^/]+/.+$")
    local_name: str | None = Field(default=None, min_length=1)

    def resolve(self, cfg: GroveConfig, title: str, ts: str) -> ResolvedBranch:
        del cfg, title, ts
        local = self.local_name or self.remote_ref.split("/", 1)[1]
        return ResolvedBranch(
            name=local,
            base_ref=self.remote_ref,
            mode=BranchMode.NEW,
            provenance=BranchProvenance.GROVE_CREATED,
            tracks=self.remote_ref,
        )


# ─── union ───────────────────────────────────────────────────────────────────


type BranchPlan = Annotated[
    AutoBranch | NewNamedBranch | ExistingLocalBranch | TrackRemoteBranch,
    Field(discriminator="kind"),
]
"""Wire-level discriminated union. Clients send any of the four variants;
Pydantic dispatches on ``kind`` with ``extra='forbid'`` rejecting typos."""


__all__ = [
    "AutoBranch",
    "BranchMode",
    "BranchPlan",
    "ExistingLocalBranch",
    "NewNamedBranch",
    "ResolvedBranch",
    "TrackRemoteBranch",
]
