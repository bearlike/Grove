"""Host-wide sidebar pins: which workspaces the user keeps at the top.

Pins are one user's preference shared by every browser, so they live in the
state directory beside the other host-wide files rather than in a client.
"""

from __future__ import annotations

import json
from pathlib import Path

from grove.core import paths
from grove.core.errors import GroveError

_VERSION = 1


class SidebarPinStore:
    """Atomic JSON persistence for the ordered pin list, newest pin first.

    Order IS the stored list order, not a timestamp: pin actions within one
    second (or one clock tick) still sort deterministically, and a re-pin of an
    already pinned id is a no-op that keeps its position. Every mutation holds
    the cross-process lock over the whole read-modify-write, like the other
    state files, so the daemon and a second process cannot drop each other's pin.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.sidebar_pins_path()

    @property
    def path(self) -> Path:
        return self._path

    def pinned(self) -> list[str]:
        """Pinned workspace ids, most recently pinned first."""
        return self._load()

    def pin(self, workspace_id: str) -> list[str]:
        """Pin at the top unless already pinned (idempotent, position kept)."""
        with paths.exclusive_lock(self._path):
            ids = self._load()
            if workspace_id not in ids:
                ids.insert(0, workspace_id)
                self._write(ids)
            return ids

    def unpin(self, workspace_id: str) -> list[str]:
        """Remove a pin; absent ids are a no-op."""
        with paths.exclusive_lock(self._path):
            ids = self._load()
            if workspace_id in ids:
                ids.remove(workspace_id)
                self._write(ids)
            return ids

    def _load(self) -> list[str]:
        if not self._path.exists():
            return []
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise GroveError(f"cannot read sidebar pins at {self._path}: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("version") != _VERSION:
            raise GroveError(f"unsupported sidebar pins file at {self._path}")
        ids = raw.get("pinned", [])
        if not isinstance(ids, list):
            raise GroveError(f"`pinned` must be a list in {self._path}")
        return [i for i in ids if isinstance(i, str)]

    def _write(self, ids: list[str]) -> None:
        payload = {"version": _VERSION, "pinned": ids}
        paths.write_atomic(self._path, json.dumps(payload, indent=2) + "\n")


__all__ = ["SidebarPinStore"]
