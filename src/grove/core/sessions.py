"""Cross-worktree agent-session exploration for one project.

One question: *which agent sessions belong to this project, and how do I read
one?* The :class:`SessionExplorer` aggregates what the adapters already know —
``list_sessions`` per scanned directory, ``locate_transcripts`` /
``read_turns`` per session — across the repo root, every git worktree, and
every Grove workspace path (including paused workspaces whose worktree is gone:
transcripts outlive worktrees). It annotates each session with its Grove
workspace and provenance, so the ``grove sessions`` CLI stays a thin renderer.

Read-only by construction: the explorer never launches, mutates, or deletes
anything — it composes the adapters' read-only scans. Adding a new agent tool
(codex, opencode) changes nothing here; ``all_adapters()`` picks up its
adapter automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from grove.core.agents import (
    SessionProvenance,
    SessionSummary,
    SessionTurn,
    all_adapters,
    get_adapter,
)
from grove.core.errors import GroveError
from grove.core.git import GitRepo, detect_root
from grove.core.manager import WorkspaceManager, build

if TYPE_CHECKING:
    from grove.core.workspace import WorkspaceState

# Aware epoch for "no mtime" rows so the newest-first sort never compares
# aware and naive datetimes (that raises, and a sort must never raise here).
_EPOCH = datetime.fromtimestamp(0, tz=UTC)


@dataclass(slots=True, frozen=True)
class SessionListing:
    """One session row with its project context attached.

    ``workspace_*`` fields are ``None`` for a session found in a directory
    Grove doesn't manage (a hand-made worktree, or the repo root with no ROOT
    workspace). ``provenance`` is ``grove_launched`` only when the id matches
    a workspace's minted ``agent_session_id``.
    """

    summary: SessionSummary
    provenance: SessionProvenance
    workspace_id: str | None = None
    workspace_title: str | None = None
    workspace_branch: str | None = None


class SessionExplorer:
    """Aggregate, filter, and resolve agent sessions across a project's worktrees."""

    def __init__(self, manager: WorkspaceManager) -> None:
        self._manager = manager

    @classmethod
    def from_cwd(cls, cwd: Path) -> SessionExplorer:
        """Build an explorer for the project enclosing ``cwd``.

        Works from inside any worktree: the *main* worktree (first entry of
        ``git worktree list``) is the root the workspace store is keyed by, so
        the explorer always binds its manager there — binding to the linked
        worktree's own root would find zero workspaces.
        """
        root = detect_root(cwd)
        if root is None:
            raise GroveError(f"{cwd} is not inside a git repository")
        main_root = GitRepo(root).worktree_paths()[0]
        return cls(build(main_root))

    @property
    def repo_root(self) -> Path:
        return self._manager.repo_root

    def scan_roots(self) -> list[Path]:
        """Every directory whose sessions belong to this project, de-duplicated.

        Union of the live ``git worktree list`` (main first; covers hand-made
        worktrees Grove never managed) and every workspace's persisted
        ``worktree_path`` (covers paused workspaces whose directory is gone —
        their transcripts still exist under the encoded-cwd projects folder).
        """
        out: list[Path] = []
        seen: set[str] = set()
        candidates = [
            *GitRepo(self._manager.repo_root).worktree_paths(),
            *(Path(state.worktree_path) for state in self._manager.list()),
        ]
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                out.append(path)
        return out

    def list(
        self,
        *,
        agent: str | None = None,
        workspace: str | None = None,
        since: datetime | None = None,
        limit: int | None = None,
    ) -> list[SessionListing]:
        """Every session across the project, newest-first, optionally filtered.

        ``agent`` matches the adapter kind exactly; ``workspace`` matches a
        workspace id prefix or a case-insensitive title substring; ``since``
        keeps sessions modified at/after that instant; ``limit`` caps the
        result after sorting.
        """
        states = self._manager.list()
        by_cwd: dict[str, WorkspaceState] = {s.worktree_path: s for s in states}
        minted: dict[str, WorkspaceState] = {
            s.agent_session_id: s for s in states if s.agent_session_id
        }

        listings: list[SessionListing] = []
        seen: set[tuple[str, str]] = set()
        for root in self.scan_roots():
            for adapter in all_adapters():
                for summary in adapter.list_sessions(root):
                    key = (summary.adapter_kind, summary.session_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    state = minted.get(summary.session_id) or by_cwd.get(str(root))
                    listings.append(
                        SessionListing(
                            summary=summary,
                            provenance=(
                                "grove_launched"
                                if summary.session_id in minted
                                else "fs_discovered"
                            ),
                            workspace_id=state.id if state else None,
                            workspace_title=state.title if state else None,
                            workspace_branch=state.branch if state else None,
                        )
                    )

        if agent is not None:
            listings = [ls for ls in listings if ls.summary.adapter_kind == agent]
        if workspace is not None:
            needle = workspace.lower()
            listings = [
                ls
                for ls in listings
                if (ls.workspace_id or "").startswith(workspace)
                or needle in (ls.workspace_title or "").lower()
            ]
        if since is not None:
            listings = [
                ls
                for ls in listings
                if ls.summary.modified_at is not None and ls.summary.modified_at >= since
            ]
        listings.sort(key=lambda ls: ls.summary.modified_at or _EPOCH, reverse=True)
        return listings[:limit] if limit is not None else listings

    def for_workspace(self, workspace_id: str) -> tuple[SessionListing, ...]:
        """Every session recorded for one workspace's directory, newest-first.

        The bounded variant of :meth:`list` for per-request consumers (the
        daemon's ``GET /workspaces/{id}/sessions``): scans only the workspace's
        own cwd instead of every worktree, so the transcript-parse cost stays
        one directory regardless of project size. Raises
        :class:`~grove.core.errors.WorkspaceNotFound` for an unknown id.

        Returns a tuple — in this class body a ``list[...]`` annotation would
        resolve to the :meth:`list` method, not the builtin (the documented
        mypy shadowing trap).
        """
        state = self._manager.get(workspace_id)
        listings: list[SessionListing] = []
        seen: set[tuple[str, str]] = set()
        for adapter in all_adapters():
            for summary in adapter.list_sessions(Path(state.worktree_path)):
                key = (summary.adapter_kind, summary.session_id)
                if key in seen:
                    continue
                seen.add(key)
                listings.append(
                    SessionListing(
                        summary=summary,
                        provenance=(
                            "grove_launched"
                            if summary.session_id == state.agent_session_id
                            else "fs_discovered"
                        ),
                        workspace_id=state.id,
                        workspace_title=state.title,
                        workspace_branch=state.branch,
                    )
                )
        listings.sort(key=lambda ls: ls.summary.modified_at or _EPOCH, reverse=True)
        return tuple(listings)

    def resolve(self, ref: str) -> SessionListing:
        """The unique session whose id matches ``ref`` exactly or by prefix.

        Raises :class:`GroveError` when nothing matches or the prefix is
        ambiguous (the message lists the candidates, so the user can extend
        the prefix without re-running ``list``).
        """
        listings = self.list()
        exact = [ls for ls in listings if ls.summary.session_id == ref]
        if exact:
            return exact[0]
        matches = [ls for ls in listings if ls.summary.session_id.startswith(ref)]
        if not matches:
            raise GroveError(f"no session matches {ref!r} in this project")
        if len(matches) > 1:
            ids = ", ".join(ls.summary.session_id for ls in matches[:8])
            raise GroveError(f"session ref {ref!r} is ambiguous: {ids}")
        return matches[0]

    def transcripts(self, listing: SessionListing) -> tuple[Path, ...]:
        """Every transcript file for the session — main thread first, then
        sub-agent files — via the owning adapter's locator."""
        summary = listing.summary
        cwd = Path(summary.cwd) if summary.cwd else summary.transcript_path.parent
        adapter = get_adapter(summary.adapter_kind)
        return tuple(adapter.locate_transcripts(cwd, summary.session_id))

    def turns_for(
        self, listing: SessionListing, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """The normalized conversation for an already-resolved listing.

        Split from :meth:`turns` so a caller holding a listing (the daemon's
        turns endpoint, fed by :meth:`for_workspace`) skips the full-project
        :meth:`resolve` scan.
        """
        adapter = get_adapter(listing.summary.adapter_kind)
        return adapter.read_turns(self.transcripts(listing), last=last)

    def turns(self, ref: str, *, last: int | None = None) -> tuple[SessionTurn, ...]:
        """The normalized conversation for the session matching ``ref``."""
        return self.turns_for(self.resolve(ref), last=last)


__all__ = ["SessionExplorer", "SessionListing"]
