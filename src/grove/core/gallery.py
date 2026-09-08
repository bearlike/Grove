"""Host-wide census of ``.drawio`` files and their browser-rendered previews.

Answers one question at host scope: *which draw.io diagrams exist on this
host, and which project, workspace and session does each belong to?* It is
the diagram sibling of :class:`~grove.core.sessions.SessionCatalog` and
borrows its whole shape — read-only by construction, metadata-only by cost,
attribution derived from paths Grove already knows rather than a filesystem
crawl.

**Where diagrams are looked for.** Every git worktree of every known repo
(``GitRepo.worktree_paths``, the same family ``SessionExplorer.scan_roots``
walks), searched through ``git ls-files`` for the tracked and untracked
``.drawio`` files git itself does not ignore — plus the worktree's
``.grove/attachments/`` tree, which IS git-excluded (that is where the
mockup skill draws) and therefore has to be walked by hand. Nothing outside
a known repo is ever visited.

**How a file is attributed.** The file's worktree names its workspace record
(the same ``by_cwd`` map the session catalog builds), and the workspace's
title, branch and reconciled status ride the row. The session is the one
recorded at that worktree whose span covers the file's last write (see
``_session_for``), of the workspace's own kind — the catalog's rows are the
source, so a file in a hand-made worktree Grove never managed still resolves
to whichever session ran there. A file that resolves
to neither is still listed with its project; nothing is dropped.

**Previews are cached by CONTENT, not by workspace.** ``DiagramPreviewFiles``
fences one PNG per workspace to one revision, which is right for a live
collaboration and wrong for a gallery: the same file may be reached through
two root-placement workspaces, and a file with no workspace at all still
needs a picture. The gallery keys its PNGs by the source's SHA-256 under the
user state dir, so a render lands once per file version and outlives the
worktree that produced it. The browser is still the only renderer.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Final
from xml.etree import ElementTree

from loguru import logger

from grove.core import paths
from grove.core.attachments import AttachmentStore
from grove.core.diagrams import MAX_DIAGRAM_BYTES, DiagramPreviewFiles
from grove.core.errors import DiagramUnavailable, WorkspaceStateError
from grove.core.git import GitRepo
from grove.core.workspace import LIVE_STATUSES, WorkspaceState, WorkspaceStatus

if TYPE_CHECKING:
    from grove.core.registry import RepoRegistry
    from grove.core.sessions import CatalogEntry

_SUFFIX: Final = ".drawio"
_PREVIEW_DIR: Final = "diagram-previews"
_HEX64: Final = frozenset("0123456789abcdef")


@dataclass(slots=True, frozen=True)
class GalleryItem:
    """One ``.drawio`` file on the host, attributed as far as the evidence goes.

    ``id`` is the SHA-256 of the file's absolute path — stable across scans,
    safe as a URL segment, and never a path a client could hand back. ``digest``
    is the SHA-256 of the file's bytes: the preview key, and what tells a client
    its cached picture is stale. ``pages`` comes from counting ``<diagram>``
    elements in the source (a bounded read, no inflate), ``None`` when the file
    could not be parsed at all.

    ``workspace_live`` is the reconciled status folded to one bit — ACTIVE or
    IDLE — because the one thing a gallery does with it is choose between
    *open the workspace* and *open the session*.
    """

    id: str
    path: Path
    relative_path: str
    repo_root: Path
    repo_name: str
    worktree_path: Path
    size_bytes: int
    modified_at: datetime
    digest: str
    pages: int | None
    workspace_id: str | None = None
    workspace_title: str | None = None
    workspace_branch: str | None = None
    workspace_live: bool = False
    session_id: str | None = None
    session_kind: str | None = None
    session_cwd: str | None = None
    session_title: str | None = None
    session_live: bool = False


class DiagramGallery:
    """Enumerate every ``.drawio`` Grove can attribute, and serve its bytes.

    Blocking by contract (``git ls-files`` per worktree plus one ``stat`` and
    one bounded read per file) — a request-scoped read the daemon runs in the
    executor behind the same short memo the session catalog uses, never on
    the activity poll.
    """

    def __init__(self, registry: RepoRegistry) -> None:
        self._registry = registry

    def item_for_path(self, path: Path, sessions: tuple[CatalogEntry, ...]) -> GalleryItem | None:
        """Materialize one known diagram path without enumerating worktrees.

        A file event already identifies the candidate. Its nearest ``.git``
        marker establishes the worktree locally; the marker's pointer establishes
        the main repository without ``git worktree list``. Workspace attribution
        reads the registry's indexed persisted states rather than reconciling all
        managers. ``None`` means the path is gone, not a diagram, or outside a
        known repository.
        """
        if path.suffix.lower() != _SUFFIX:
            return None
        worktree = self._worktree_for(path)
        if worktree is None:
            return None
        root = self._main_root(worktree)
        known_roots = {known.resolve() for known in self._registry.known_roots()}
        if root.resolve() not in known_roots:
            return None
        states = [
            state
            for state in self._registry.workspace_states()
            if Path(state.worktree_path).resolve() == worktree.resolve()
        ]
        return self._item(
            root,
            worktree,
            path,
            {worktree: states},
            self._sessions_by_cwd(sessions),
        )

    def scan(self, sessions: tuple[CatalogEntry, ...]) -> tuple[GalleryItem, ...]:
        """Every diagram on the host, newest-first by mtime.

        ``sessions`` is the catalog's own scan, handed in rather than re-run:
        the daemon already memoizes it, and the session-per-worktree join
        below is the only reason the gallery needs it.
        """
        by_worktree = self._workspaces_by_worktree()
        sessions_by_cwd = self._sessions_by_cwd(sessions)
        items: list[GalleryItem] = []
        seen: set[Path] = set()
        # A TRACKED diagram is checked out into every worktree of its repo, so
        # without this a repo with eight workspaces lists it eight times under
        # eight attributions. One row per (repo, path, content): the main
        # worktree comes first in git's order and wins; a worktree that has
        # actually CHANGED the file carries a different digest and keeps its row.
        seen_content: set[tuple[Path, str, str]] = set()
        for root in self._registry.known_roots():
            if not root.is_dir():
                continue
            try:
                worktrees = GitRepo(root).worktree_paths()
            except Exception as exc:
                logger.warning(
                    "gallery bootstrap skipped unavailable repository: {}", type(exc).__name__
                )
                continue
            for worktree in worktrees:
                # `git worktree list` reports every registered worktree, including
                # one a CONTAINER registered under its own namespace (`/workspaces/…`)
                # that does not exist on this host — and `subprocess.run(cwd=…)`
                # raises before git can say so. Measured on the reference host.
                if not worktree.is_dir():
                    continue
                for path in self._diagram_paths(worktree):
                    if path in seen:
                        continue
                    seen.add(path)
                    item = self._item(root, worktree, path, by_worktree, sessions_by_cwd)
                    if item is None:
                        continue
                    content_key = (root, item.relative_path, item.digest)
                    if content_key in seen_content:
                        continue
                    seen_content.add(content_key)
                    items.append(item)
        items.sort(key=lambda item: item.modified_at, reverse=True)
        return tuple(items)

    @staticmethod
    def read_source(item: GalleryItem) -> bytes:
        """The file's bytes, bounded, refusing a link swapped in since the scan."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(item.path, flags)
        except OSError as exc:
            raise DiagramUnavailable("diagram file is unavailable") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise DiagramUnavailable("diagram file is unavailable")
            with os.fdopen(fd, "rb", closefd=False) as source:
                data = source.read(MAX_DIAGRAM_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_DIAGRAM_BYTES:
            raise WorkspaceStateError("diagram source exceeds the 5 MiB limit")
        return data

    # ── previews, keyed by content ────────────────────────────────────────

    @staticmethod
    def preview_path(digest: str) -> Path:
        """Where the PNG for one content digest lives under the state dir."""
        if len(digest) != 64 or not set(digest) <= _HEX64:
            raise WorkspaceStateError("diagram digest must be 64 hex characters")
        return paths.user_state_path().parent / _PREVIEW_DIR / f"{digest}.png"

    @classmethod
    def read_preview(cls, digest: str) -> bytes | None:
        """The cached PNG for *digest*, or ``None`` when no render has landed."""
        path = cls.preview_path(digest)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise DiagramUnavailable("diagram preview is unavailable") from exc
        if not stat.S_ISREG(info.st_mode):
            raise DiagramUnavailable("diagram preview is unavailable")
        png = path.read_bytes()
        DiagramPreviewFiles.validate_png(png)
        return png

    @classmethod
    def write_preview(cls, digest: str, png: bytes) -> Path:
        """Persist a browser-rendered PNG for *digest*; a later render replaces it."""
        DiagramPreviewFiles.validate_png(png)
        path = cls.preview_path(digest)
        paths.ensure_dir(path.parent)
        stage = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        stage.write_bytes(png)
        os.replace(stage, path)
        return path

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _worktree_for(path: Path) -> Path | None:
        """The containing worktree, found by a local ``.git`` walk-up only."""
        try:
            resolved = path.resolve()
        except OSError:
            return None
        current = resolved.parent
        while True:
            if (current / ".git").exists():
                return current
            parent = current.parent
            if parent == current:
                return None
            current = parent

    @staticmethod
    def _main_root(worktree: Path) -> Path:
        """The main repository root for a worktree without shelling out to git."""
        marker = worktree / ".git"
        if not marker.is_file():
            return worktree
        try:
            line = marker.read_text(encoding="utf-8").strip()
        except OSError:
            return worktree
        if not line.startswith("gitdir:"):
            return worktree
        target = Path(line.removeprefix("gitdir:").strip())
        if not target.is_absolute():
            target = (marker.parent / target).resolve()
        parts = target.parts
        if ".git" not in parts:
            return worktree
        index = len(parts) - 1 - parts[::-1].index(".git")
        return Path(*parts[:index])

    def _workspaces_by_worktree(self) -> dict[Path, list[WorkspaceState]]:
        """Worktree path → every (reconciled) workspace record living there.

        ``manager.list()`` reconciles status, which is what ``workspace_live``
        reads. Several ROOT-placement workspaces share the repo root, which is
        why this is a list: the session that produced a file decides which of
        them the row names (see ``_item``), not whichever was listed first.

        THE PER-REPO BODY IS GUARDED, because ``registry.get`` resolves that
        repo's whole config cascade and raises ``ConfigError`` on invalid JSON.
        Unguarded, one repo's stray comma took down every caller of this scan —
        and since the daemon now bootstraps the gallery during startup, that
        meant a single bad project config made the whole daemon fail to start.
        The same rule the activity snapshot already follows: a degraded repo
        degrades alone.
        """
        out: dict[Path, list[WorkspaceState]] = {}
        for root in self._registry.known_roots():
            try:
                manager = self._registry.get(root)
                states = manager.list()
            except Exception as exc:
                logger.warning("gallery skipped unreadable repository: {}", type(exc).__name__)
                continue
            for state in states:
                out.setdefault(Path(state.worktree_path), []).append(state)
        return out

    @staticmethod
    def _sessions_by_cwd(sessions: tuple[CatalogEntry, ...]) -> dict[str, list[CatalogEntry]]:
        """cwd → every catalog row recorded there, newest-first as the scan sorts them."""
        out: dict[str, list[CatalogEntry]] = {}
        for entry in sessions:
            if entry.ref.cwd is not None:
                out.setdefault(entry.ref.cwd, []).append(entry)
        return out

    @staticmethod
    def _session_for(candidates: list[CatalogEntry], modified: float) -> CatalogEntry | None:
        """The session that was RUNNING when the file was last written.

        A worktree hosts many sessions over its life (a root-placement
        workspace's cwd is the repo root, shared by every session anyone ever
        ran there), so "the newest" would credit every old diagram to whoever
        opened the directory last. Prefer the session whose span (birth → last
        transcript write) covers the file's mtime and that started LATEST —
        the one most recently begun when the file was saved; failing that, the
        latest-started session that had begun by then; and only for a file
        older than every transcript, the oldest one.
        """
        if not candidates:
            return None
        dated = [c for c in candidates if c.ref.birth is not None]
        dated.sort(key=lambda c: c.ref.birth.timestamp() if c.ref.birth else 0.0, reverse=True)
        for entry in dated:
            assert entry.ref.birth is not None
            born = entry.ref.birth.timestamp()
            if born <= modified <= entry.ref.mtime:
                return entry
        for entry in dated:
            assert entry.ref.birth is not None
            if entry.ref.birth.timestamp() <= modified:
                return entry
        return candidates[-1]

    @staticmethod
    def _diagram_paths(worktree: Path) -> list[Path]:
        """Every ``.drawio`` under *worktree*: git's view plus the excluded attachments tree."""
        found: list[Path] = []
        for rel in GitRepo(worktree).list_files(worktree, "*.drawio"):
            if rel.lower().endswith(_SUFFIX):
                found.append(worktree / rel)
        attachments = AttachmentStore.root(worktree)
        if attachments.is_dir():
            for dirpath, dirnames, filenames in os.walk(attachments):
                dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
                for name in filenames:
                    if name.lower().endswith(_SUFFIX):
                        found.append(Path(dirpath) / name)
        return found

    def _item(
        self,
        root: Path,
        worktree: Path,
        path: Path,
        by_worktree: dict[Path, list[WorkspaceState]],
        sessions_by_cwd: dict[str, list[CatalogEntry]],
    ) -> GalleryItem | None:
        try:
            info = os.lstat(path)
        except OSError:
            return None
        if not stat.S_ISREG(info.st_mode):
            return None
        if info.st_size > MAX_DIAGRAM_BYTES:
            logger.debug("gallery: skipping oversized diagram {}", path)
            return None
        try:
            source = path.read_bytes()
        except OSError:
            return None
        session = self._session_for(sessions_by_cwd.get(str(worktree), []), info.st_mtime)
        states = by_worktree.get(worktree, [])
        # The session decides the workspace when it can: the catalog already
        # attributed the session to a record (minted id first, cwd second), and
        # naming a DIFFERENT record for the same file would put two answers to
        # one question on one card. With no session, the first record wins —
        # the same tie the session catalog accepts.
        state: WorkspaceState | None = None
        if session is not None and session.workspace_id is not None:
            state = next((s for s in states if s.id == session.workspace_id), None)
        if state is None:
            state = states[0] if states else None
        # Kind-gated like the catalog's own attribution: a foreign-kind
        # transcript sharing a workspace's worktree is not that workspace's.
        if (
            state is not None
            and session is not None
            and state.agent_kind
            and session.ref.adapter_kind != str(state.agent_kind)
        ):
            session = None
        return GalleryItem(
            id=hashlib.sha256(os.fsencode(str(path))).hexdigest(),
            path=path,
            relative_path=PurePosixPath(path.relative_to(worktree)).as_posix(),
            repo_root=root,
            repo_name=root.name,
            worktree_path=worktree,
            size_bytes=info.st_size,
            modified_at=datetime.fromtimestamp(info.st_mtime, tz=UTC),
            digest=hashlib.sha256(source).hexdigest(),
            pages=self._count_pages(source),
            workspace_id=state.id if state else None,
            workspace_title=state.title if state else None,
            workspace_branch=state.branch if state else None,
            workspace_live=bool(
                state and state.status in (LIVE_STATUSES | {WorkspaceStatus.RUNNING})
            ),
            session_id=session.ref.session_id if session else None,
            session_kind=session.ref.adapter_kind if session else None,
            session_cwd=session.ref.cwd if session else None,
            session_title=session.workspace_title if session else None,
            session_live=session.live if session else False,
        )

    @staticmethod
    def _count_pages(source: bytes) -> int | None:
        """``<diagram>`` elements under ``<mxfile>``; ``None`` for unparseable XML.

        Only the outer shell is parsed — a compressed page body is text, not
        a child element, so no inflate is paid here.
        """
        head = source.upper()
        if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
            return None
        try:
            tree = ElementTree.fromstring(source)
        except ElementTree.ParseError:
            return None
        if tree.tag.rsplit("}", 1)[-1] != "mxfile":
            return None
        return sum(1 for node in tree if node.tag.rsplit("}", 1)[-1] == "diagram")


__all__ = ["DiagramGallery", "GalleryItem"]
