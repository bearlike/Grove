"""Bring a repository into Grove: create it, clone it, or adopt it from disk.

One concern: turn an add-project request into a git repository on this host and
an entry in the user config's `projects` list. Registration is the existing
`add_known_project`, so nothing here learns how a project reaches a client: the
daemon's user-config watch already turns that write into a live project list.

Every refusal happens before the first write. Grove never creates into, clones
over or deletes a directory it did not just make itself, and a failed clone
removes only the directory that clone created.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from grove.core.config import add_known_project
from grove.core.contracts.projects import (
    AddProjectRequest,
    AdoptProject,
    CloneProject,
    CreateProject,
    ProjectCandidatesView,
    ProjectCandidateView,
)
from grove.core.errors import GitError, ProjectTargetRefused
from grove.core.git import GitRepo


@dataclass(slots=True)
class ProjectOnboarding:
    """Create, clone or adopt one repository, then register it.

    `projects_root` is the default parent for create and clone and the directory
    the candidate scan reads.
    """

    projects_root: str

    # ─── resolution ────────────────────────────────────────────────────────

    def root(self) -> Path:
        """The configured parent directory, `~` expanded (it may not exist yet)."""
        return Path(os.path.expanduser(self.projects_root)).resolve()

    def _parent(self, location: str | None) -> Path:
        if location is None or not location.strip():
            return self.root()
        expanded = Path(os.path.expanduser(location.strip()))
        if not expanded.is_absolute():
            raise ProjectTargetRefused(
                f"location {location!r} must be an absolute path or start with ~"
            )
        return expanded.resolve()

    @staticmethod
    def name_from_url(url: str) -> str:
        """The folder `git clone` itself would pick: the last path segment, minus `.git`."""
        segments = [s for s in re.split(r"[/:]", url.rstrip("/")) if s]
        # A local `<repo>/.git` clones into `<repo>`, as git itself does.
        if len(segments) > 1 and segments[-1] == ".git":
            segments.pop()
        tail = segments[-1] if segments else ""
        name = tail[:-4] if tail.endswith(".git") else tail
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", name):
            raise ProjectTargetRefused(
                f"cannot derive a folder name from {url!r}; enter one explicitly"
            )
        return name

    @staticmethod
    def _fresh_target(parent: Path, name: str) -> Path:
        target = parent / name
        if target.exists() or target.is_symlink():
            raise ProjectTargetRefused(f"{target} already exists; choose another name")
        return target

    # ─── the three methods ─────────────────────────────────────────────────

    def add(self, request: AddProjectRequest) -> Path:
        """Materialize the request's repository and register it; returns its root."""
        if isinstance(request, CreateProject):
            repo_root = self._create(request)
        elif isinstance(request, CloneProject):
            repo_root = self._clone(request)
        else:
            repo_root = self._adopt(request)
        add_known_project(repo_root)
        return repo_root

    def _create(self, request: CreateProject) -> Path:
        target = self._fresh_target(self._parent(request.location), request.name)
        target.mkdir(parents=True)
        try:
            GitRepo.init_with_empty_commit(target)
        except GitError:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return target.resolve()

    def _clone(self, request: CloneProject) -> Path:
        name = request.name or self.name_from_url(request.url)
        parent = self._parent(request.location)
        target = self._fresh_target(parent, name)
        parent.mkdir(parents=True, exist_ok=True)
        try:
            GitRepo.clone(request.url, target)
        except GitError as exc:
            # `git clone` removes its own partial directory on most failures;
            # this covers the timeout kill, which gives it no chance to.
            shutil.rmtree(target, ignore_errors=True)
            raise GitError(self.redact(str(exc))) from None
        return target.resolve()

    @staticmethod
    def redact(text: str) -> str:
        """Hide `user:secret@` in any URL, because git echoes the URL it was given."""
        return re.sub(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@", r"\1***@", text)

    @staticmethod
    def _adopt(request: AdoptProject) -> Path:
        path = Path(os.path.expanduser(request.path.strip()))
        if not path.is_absolute():
            raise ProjectTargetRefused(f"{request.path!r} must be an absolute path")
        if not path.is_dir():
            raise ProjectTargetRefused(f"{path} is not a directory")
        root = GitRepo.detect_root(path)
        if root is None:
            raise ProjectTargetRefused(f"{path} is not inside a git repository")
        return root

    # ─── the candidate scan ────────────────────────────────────────────────

    def candidates(self, known_roots: set[Path]) -> ProjectCandidatesView:
        """Git repositories directly under the root that `known_roots` does not hold.

        One level deep and a `.git` stat per entry, the same cheap test the
        registry uses for declared roots; the only subprocess is one remote read
        per unregistered repository, which is the list a person reads anyway.
        """
        root = self.root()
        rows: list[ProjectCandidateView] = []
        if root.is_dir():
            for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith(".") or not (entry / ".git").exists():
                    continue
                resolved = entry.resolve()
                if resolved in known_roots:
                    continue
                remotes = GitRepo(resolved).remote_urls()
                rows.append(
                    ProjectCandidateView(
                        name=entry.name,
                        path=str(resolved),
                        remote=remotes[0] if remotes else None,
                    )
                )
        return ProjectCandidatesView(root=str(root), candidates=tuple(rows))


__all__ = ["ProjectOnboarding"]
