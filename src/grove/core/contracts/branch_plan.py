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

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from grove.core.config import GroveConfig
from grove.core.workspace import BranchProvenance, Placement, slug

_FROZEN = ConfigDict(extra="forbid", frozen=True, validate_default=True)

#: Every branch name Grove will hand to git, on every variant.
#:
#: The first character excludes `-` so the value can never be parsed as a CLI
#: flag downstream. That is not defence in depth, it is the whole defence:
#: `git.py` builds argv as a list with `shell=False`, which stops *shell*
#: injection and does nothing here, because the value **is** the flag rather
#: than being embedded in one. Quoting is irrelevant; only validation stops it.
#:
#: Measured against real git 2.43, in a throwaway repo:
#:
#: * `-b -m <path> origin/main` renamed the CHECKED-OUT branch and moved HEAD
#: * `-b -D <path> feature/x` printed `Deleted branch feature/x` — permanently
#:
#: and both then exited `fatal:`, so the caller sees a failure while the damage
#: is already done. The mechanism is that `git worktree add` passes the `-b`
#: value to an internal `git branch` that re-parses it as its own argv, which
#: is why `--`/`--end-of-options` cannot be threaded in to fix it.
#:
#: The split (first char, then the rest) avoids needing a `(?!-)` lookahead —
#: Pydantic v2 uses the Rust regex engine, which does not support one.
BRANCH_NAME_PATTERN = r"^[A-Za-z0-9._/][A-Za-z0-9._/\-]*$"

#: Compiled once for the DERIVED-name check, which `Field(pattern=...)` cannot
#: reach — Pydantic validates fields, and the hazard there is a value computed
#: from two of them.
_BRANCH_NAME_RE = re.compile(BRANCH_NAME_PATTERN)


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

    placement: Placement = Placement.WORKTREE
    """Worktree vs. root. Defaults to WORKTREE so the four worktree variants
    stay untouched; only `RootBranch` sets `ROOT`. When `ROOT`, `name` is the
    empty string — the manager substitutes the live HEAD branch, since this IR
    has no git access at resolve time."""


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
    name: str = Field(min_length=1, pattern=BRANCH_NAME_PATTERN)
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
    # Verified unreachable rather than assumed: git refuses to CREATE a
    # `-`-leading branch (`fatal: '-weird' is not a valid branch name`), so
    # this path — which only ever checks out a branch that already exists —
    # has no way to be handed one. Patterned anyway, because "no variant of
    # this union may name a branch git could read as a flag" is a property
    # worth being true by construction rather than by an argument that has to
    # be re-derived every time someone adds a call site.
    name: str = Field(min_length=1, pattern=BRANCH_NAME_PATTERN)

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
    local_name: str | None = Field(default=None, min_length=1, pattern=BRANCH_NAME_PATTERN)

    @model_validator(mode="after")
    def _validate_effective_local_name(self) -> TrackRemoteBranch:
        """The DERIVED default is an injection vector too, and a reachable one.

        Patterning `local_name` alone leaves this variant exploitable with the
        field absent entirely: `remote_ref` is only shaped `^[^/]+/.+$`, so
        `x/-D` passes it and the default derivation hands git `-D`. Proven in a
        throwaway repo — `git worktree add -b -D <path> x/-D` printed
        `Deleted branch x/-D` and then `fatal:`. `x/-D` is a legal ref name
        (`git check-ref-format` accepts it and `git branch` creates it), so this
        is a real shape rather than a theoretical one.

        Validated here rather than in `resolve()` because a contract's job is to
        reject bad intent at the boundary, before anything downstream can act on
        it — and `resolve()` is already past the point where a client's input
        stopped being questionable.
        """
        if not _BRANCH_NAME_RE.match(self.effective_local_name):
            raise ValueError(
                f"local branch name {self.effective_local_name!r} must match "
                f"{BRANCH_NAME_PATTERN} (a leading '-' would be read as a git flag)"
            )
        return self

    @property
    def effective_local_name(self) -> str:
        """The local branch this plan will actually create.

        One definition, so the validator above and :meth:`resolve` cannot
        disagree about which string reaches git — a validator that checks a
        different value than the one used is worse than no validator.
        """
        return self.local_name or self.remote_ref.split("/", 1)[1]

    def resolve(self, cfg: GroveConfig, title: str, ts: str) -> ResolvedBranch:
        del cfg, title, ts
        local = self.effective_local_name
        return ResolvedBranch(
            name=local,
            base_ref=self.remote_ref,
            mode=BranchMode.NEW,
            provenance=BranchProvenance.GROVE_CREATED,
            tracks=self.remote_ref,
        )


class RootBranch(BaseModel):
    """Run the workspace in the repo root itself — no worktree, current branch.

    The fifth variant is a placement choice, not a branch choice: it carries
    no user fields because there is nothing to source. The session is rooted at
    the repo, adopting whatever branch HEAD already points to; Grove creates no
    worktree and no branch, so kill never deletes anything and pause/resume are
    refused. This is "work in place on what I've already got out" — the escape
    hatch for users who don't want an isolated worktree per task.

    `resolve()` returns a sentinel with an empty `name` (the manager fills it
    from live HEAD) and `provenance=USER_ATTACHED`, so even an explicit
    `delete_branch=True` on kill is overridden to False: the user's working
    branch is never Grove's to delete.
    """

    model_config = _FROZEN

    kind: Literal["root"] = "root"

    def resolve(self, cfg: GroveConfig, title: str, ts: str) -> ResolvedBranch:
        del cfg, title, ts
        return ResolvedBranch(
            name="",  # manager substitutes the live HEAD branch
            base_ref=None,
            mode=BranchMode.CHECKOUT,
            provenance=BranchProvenance.USER_ATTACHED,
            placement=Placement.ROOT,
        )


# ─── union ───────────────────────────────────────────────────────────────────


type BranchPlan = Annotated[
    AutoBranch | NewNamedBranch | ExistingLocalBranch | TrackRemoteBranch | RootBranch,
    Field(discriminator="kind"),
]
"""Wire-level discriminated union. Clients send any of the five variants;
Pydantic dispatches on ``kind`` with ``extra='forbid'`` rejecting typos.
Four variants produce a worktree; ``RootBranch`` runs in the repo root."""


__all__ = [
    "AutoBranch",
    "BranchMode",
    "BranchPlan",
    "ExistingLocalBranch",
    "NewNamedBranch",
    "ResolvedBranch",
    "RootBranch",
    "TrackRemoteBranch",
]
