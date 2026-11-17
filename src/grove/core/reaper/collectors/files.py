"""Per-workspace residue under Grove's own state and config directories.

Six kinds, each named for its owner by the accessor in :mod:`grove.core.paths`
that writes it: agent exit records, init and provision logs, native worker
configs, retired first-turn briefs and per-workspace agent config dirs (all
keyed by workspace id), and hook sidecars (keyed by agent session id). Every
directory is resolved through those accessors, so a test's redirect of one
covers the reaper too, and nothing outside them is ever listed.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import ClassVar

from grove.core import paths
from grove.core.errors import GroveError
from grove.core.reaper.ledger import ResourceClass, ResourceRef

#: A stand-in workspace id, so each accessor names both its directory and the
#: suffix it appends to an id.
_PROBE = "0"

#: The per-workspace accessors whose files are keyed by workspace id. Named, and
#: looked up on :mod:`paths` per call, so a redirect of one reaches the reaper.
_WORKSPACE_FILES: tuple[str, ...] = (
    "agent_exit_path",
    "init_log_path",
    "provision_log_path",
    "native_worker_config_path",
    "agent_brief_path",
)

#: A hook sidecar is ``<session id>.json``.
_SIDECAR_SUFFIX = ".json"


class FilesCollector:
    """Lists Grove's per-workspace files and directories, and removes one.

    A ref's ``workspace_id`` is its OWNER: the workspace id in its name, or for
    a sidecar the workspace the usage store recorded the session under, else
    the session id itself. The ledger matches owners against every record's id
    and launched session id.

    An agent config dir holding a real transcript is never listed, so it is
    never a candidate. ``kept`` names those the last scan saw.
    """

    cls: ClassVar[ResourceClass] = "files"
    GRACE: ClassVar[timedelta] = timedelta(days=1)
    #: A transcript at or under this size holds no real turn. Measured
    #: 2026-10-21: 353 of 8,175 agent config dirs held a transcript, and the 89
    #: transcripts over 1 KB sat in 18 of them, so the floor keeps every
    #: conversation and frees the rest, which hold a seeded ``.claude.json``.
    TRANSCRIPT_FLOOR: ClassVar[int] = 1024

    def __init__(self) -> None:
        self.kept: tuple[Path, ...] = ()

    def found(self) -> list[ResourceRef]:
        refs: list[ResourceRef] = []
        for accessor in _WORKSPACE_FILES:
            directory, suffix = self._layout(accessor)
            refs.extend(
                ResourceRef("files", str(entry), entry.name.removesuffix(suffix))
                for entry in self._entries(directory)
                if entry.name.endswith(suffix) and entry.is_file()
            )
        sessions = self._session_workspaces()
        refs.extend(
            ResourceRef("files", str(entry), sessions.get(entry.stem, entry.stem))
            for entry in self._entries(paths.agent_sidecar_dir())
            if entry.name.endswith(_SIDECAR_SUFFIX) and entry.is_file()
        )
        kept: list[Path] = []
        for entry in self._entries(self._config_root()):
            if not entry.is_dir():
                continue
            if self.holds_transcript(entry):
                kept.append(entry)
            else:
                refs.append(ResourceRef("files", str(entry), entry.name))
        self.kept = tuple(kept)
        return refs

    def remove(self, ref: ResourceRef) -> None:
        path = Path(ref.key)
        # Re-derived at the moment of acting: only a path this collector would
        # list may be removed, and a transcript written since the mark wins.
        if not self._recognises(path):
            raise GroveError(f"refusing to remove {path}: not a Grove per-workspace file")
        try:
            if path.parent == self._config_root():
                if self.holds_transcript(path):
                    raise GroveError(f"kept: {path} holds a transcript")
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
        except OSError as exc:
            raise GroveError(f"could not remove {path}: {exc}") from exc

    @classmethod
    def holds_transcript(cls, config_dir: Path) -> bool:
        """Whether an agent config dir holds a transcript with at least one real turn."""
        try:
            return any(
                transcript.stat().st_size > cls.TRANSCRIPT_FLOOR
                for transcript in (config_dir / "projects").rglob("*.jsonl")
            )
        except OSError:
            # Unreadable is not evidence of absence.
            return True

    def _recognises(self, path: Path) -> bool:
        if path.parent == self._config_root():
            return True
        if path.parent == paths.agent_sidecar_dir():
            return path.name.endswith(_SIDECAR_SUFFIX)
        return any(
            path.parent == directory and path.name.endswith(suffix)
            for directory, suffix in map(self._layout, _WORKSPACE_FILES)
        )

    @staticmethod
    def _layout(accessor: str) -> tuple[Path, str]:
        resolve: Callable[[str], Path] = getattr(paths, accessor)
        probe = resolve(_PROBE)
        return probe.parent, probe.name.removeprefix(_PROBE)

    @staticmethod
    def _config_root() -> Path:
        return paths.agent_workspace_config_dir(_PROBE).parent

    @staticmethod
    def _entries(directory: Path) -> list[Path]:
        try:
            return list(directory.iterdir())
        except OSError:
            return []

    @staticmethod
    def _session_workspaces() -> dict[str, str]:
        """Session id → workspace id, as the usage store recorded them.

        Read-only and best-effort: the store is a derived cache, and an
        unreadable one only means a sidecar is owned by its session id.
        """
        path = paths.usage_db_path()
        if not path.exists():
            return {}
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
                rows = db.execute(
                    "SELECT session_id, workspace_id FROM sessions WHERE workspace_id IS NOT NULL"
                ).fetchall()
        except sqlite3.Error:
            return {}
        return dict(rows)


__all__ = ["FilesCollector"]
