"""Adding a project: create one, clone one, or adopt one already on disk.

`AddProjectRequest` is a discriminated union in the `BranchPlan` shape, because
the three methods take different fields and a single model with optional fields
would accept a clone with no URL. Every method ends in the same place, the
user config's `projects` list, so the response is the existing `ProjectView`.

`ProjectCandidatesView` is the read the "existing repository" step lists: git
repositories directly under a directory that Grove does not list yet. It states
the directory it scanned, so a client never has to guess which root it got.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from grove.core.contracts.views import ProjectView

_FROZEN = ConfigDict(extra="forbid", frozen=True)

# One path segment: no separators, no `.`/`..`, no leading dash (a name that
# becomes a path an argv later reads must never look like a flag).
ProjectName = Annotated[
    str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")
]


class CreateProject(BaseModel):
    """Start an empty repository: `git init` plus one empty initial commit."""

    model_config = _FROZEN

    kind: Literal["create"] = "create"
    name: ProjectName
    location: str | None = None
    """Parent directory. Omitted means the configured `projects_root`."""


class CloneProject(BaseModel):
    """Clone a repository the host's git can already reach."""

    model_config = _FROZEN

    kind: Literal["clone"] = "clone"
    url: Annotated[str, Field(min_length=1, max_length=2000, pattern=r"^[^-\s][^\s]*$")]
    name: ProjectName | None = None
    """Folder name. Omitted means the name derived from the URL."""
    location: str | None = None
    """Parent directory. Omitted means the configured `projects_root`."""


class AdoptProject(BaseModel):
    """Register a repository that already exists on this host."""

    model_config = _FROZEN

    kind: Literal["adopt"] = "adopt"
    path: Annotated[str, Field(min_length=1, max_length=4096)]
    """An absolute path to a git repository (any directory inside one works)."""


AddProjectRequest = Annotated[
    CreateProject | CloneProject | AdoptProject, Field(discriminator="kind")
]


class ProjectCandidateView(BaseModel):
    """One git repository on disk that Grove does not list yet."""

    model_config = _FROZEN

    name: str
    path: str
    remote: str | None = None
    """The first configured remote URL, when the repository has one."""


class ProjectCandidatesView(BaseModel):
    """Unregistered repositories directly under `root`, sorted by name."""

    model_config = _FROZEN

    root: str
    """The directory that was scanned, `~` expanded."""
    candidates: tuple[ProjectCandidateView, ...] = Field(default_factory=tuple)


__all__ = [
    "AddProjectRequest",
    "AdoptProject",
    "CloneProject",
    "CreateProject",
    "ProjectCandidateView",
    "ProjectCandidatesView",
    "ProjectView",
]
