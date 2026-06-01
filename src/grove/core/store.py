"""Single-file JSON persistence for WorkspaceState records.

One global `state.json` (path from `paths.user_state_path()`) holds every
workspace, keyed internally by workspace id. Filtering by repo is a
public method, so callers stay agnostic of the storage layout.

Schema is versioned (currently `1`) — when the field set changes, bump the
version and write a migration step here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core import paths
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.workspace import (
    PERSISTED_STATUSES,
    BranchProvenance,
    InitStatus,
    WorkspaceState,
    WorkspaceStatus,
)

_VERSION = 1

# Legacy on-disk status values that have been removed from the enum but may
# still appear in older state.json files. Each maps to the persisted intent
# that originally produced it; reconciliation at read time will re-derive the
# user-visible status (e.g. legacy `stale` → RUNNING intent → OFFLINE on read).
_LEGACY_STATUS_ALIASES: dict[str, WorkspaceStatus] = {
    "stale": WorkspaceStatus.RUNNING,
}


class JsonWorkspaceStore:
    """Atomic, single-file JSON store for workspaces.

    Single writer assumed (one Grove process per user). No file locking.
    Atomicity comes from temp-file + `os.replace`, which is atomic on
    POSIX and Windows since Python 3.3.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.user_state_path()

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> list[WorkspaceState]:
        if not self._path.exists():
            return []
        try:
            with self._path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise GroveError(f"corrupt state file at {self._path}: {exc}") from exc
        except OSError as exc:
            raise GroveError(f"cannot read state file at {self._path}: {exc}") from exc

        if not isinstance(data, dict):
            raise GroveError(f"unexpected state shape at {self._path}")
        if data.get("version") != _VERSION:
            raise GroveError(
                f"state version {data.get('version')!r} not supported (expected {_VERSION})"
            )
        raw = data.get("workspaces", {})
        if not isinstance(raw, dict):
            raise GroveError(f"`workspaces` must be an object in {self._path}")
        return [self._deserialize(v) for v in raw.values()]

    def get(self, workspace_id: str) -> WorkspaceState:
        for state in self.load_all():
            if state.id == workspace_id:
                return state
        raise WorkspaceNotFound(workspace_id)

    def save(self, state: WorkspaceState) -> None:
        """Insert or replace by id.

        Refuses to persist computed statuses (ACTIVE / IDLE / OFFLINE /
        ORPHANED) — those are derived at read time, never written. Catching
        the bad write here means a manager-level bug can't pollute state.json.
        """
        if state.status not in PERSISTED_STATUSES:
            raise GroveError(
                f"refusing to persist computed status {state.status!r} for {state.id}; "
                f"only {sorted(s.value for s in PERSISTED_STATUSES)} round-trip"
            )
        records = {s.id: s for s in self.load_all()}
        records[state.id] = state
        self._write(records.values())

    def delete(self, workspace_id: str) -> None:
        records = {s.id: s for s in self.load_all()}
        if workspace_id not in records:
            raise WorkspaceNotFound(workspace_id)
        records.pop(workspace_id)
        self._write(records.values())

    def for_repo(self, repo_root: Path) -> list[WorkspaceState]:
        """All workspaces whose `repo_root` matches the given canonical path."""
        target = str(Path(repo_root).resolve())
        return [s for s in self.load_all() if s.repo_root == target]

    def list_repo_roots(self) -> list[Path]:
        """Distinct repo roots across all persisted workspaces.

        Used by the HTTP daemon to enumerate which Managers it needs to
        instantiate when serving ``GET /workspaces`` (multi-repo aggregation).
        """
        return list({Path(s.repo_root) for s in self.load_all()})

    # ─── internal ──────────────────────────────────────────────────────────

    def _write(self, states: Iterable[WorkspaceState]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {s.id: self._serialize(s) for s in states}
        payload: dict[str, Any] = {
            "version": _VERSION,
            "workspaces": serialized,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, self._path)
        logger.debug("state saved: {} workspace(s) → {}", len(serialized), self._path)

    @staticmethod
    def _serialize(state: WorkspaceState) -> dict[str, Any]:
        data = asdict(state)
        data["status"] = state.status.value
        data["created_at"] = state.created_at.isoformat()
        data["updated_at"] = state.updated_at.isoformat()
        data["paused_at"] = state.paused_at.isoformat() if state.paused_at else None
        data["init_status"] = state.init_status.value if state.init_status else None
        data["branch_provenance"] = state.branch_provenance.value
        return data

    @staticmethod
    def _deserialize(data: dict[str, Any]) -> WorkspaceState:
        # `.get()` for fields added after v1 shipped — older state.json files
        # written before init_status / branch_provenance existed must still
        # load cleanly. branch_provenance defaults to GROVE_CREATED for
        # legacy records, matching historical behavior ("Grove always
        # created the branch").
        init_status_raw = data.get("init_status")
        return WorkspaceState(
            id=data["id"],
            title=data["title"],
            repo_root=data["repo_root"],
            branch=data["branch"],
            base_branch=data["base_branch"],
            worktree_path=data["worktree_path"],
            tmux_session=data["tmux_session"],
            agent_name=data["agent_name"],
            status=_decode_status(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            paused_at=(
                datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None
            ),
            error_detail=data.get("error_detail"),
            description=data.get("description"),
            init_env=dict(data.get("init_env") or {}),
            init_status=InitStatus(init_status_raw) if init_status_raw else None,
            init_duration_ms=data.get("init_duration_ms"),
            init_log_path=data.get("init_log_path"),
            branch_provenance=BranchProvenance(
                data.get("branch_provenance", BranchProvenance.GROVE_CREATED.value)
            ),
        )


def _decode_status(raw: str) -> WorkspaceStatus:
    """Coerce on-disk status value to a persistable enum.

    Handles legacy values from older state.json files (e.g. `stale` from when
    that was a real enum member) by mapping them to the persisted intent that
    originally produced them. Reconciliation at read time will re-derive the
    user-visible status. Computed values that somehow ended up persisted (a
    bug) coerce to RUNNING so the manager can re-resolve them.
    """
    if raw in _LEGACY_STATUS_ALIASES:
        return _LEGACY_STATUS_ALIASES[raw]
    try:
        status = WorkspaceStatus(raw)
    except ValueError:
        # Unknown value — assume the intent was RUNNING and let reconciliation
        # demote it appropriately.
        return WorkspaceStatus.RUNNING
    if status not in PERSISTED_STATUSES:
        # Computed value snuck onto disk; treat as a RUNNING intent.
        return WorkspaceStatus.RUNNING
    return status
