"""A remote workspace's transcript, mirrored onto this host by append.

One question: where does a replicated transcript file live on the host, and is
this chunk the next bytes of it? A remote workload shares no filesystem with
the daemon, and every transcript reader here (the activity blend, ``/turns``,
the todo axis, the usage audit) is a file reader. Rather than teach each of
them a second road, the worker follows its own provider's transcript files by
byte offset and posts the appended bytes, and the host lays them out at the
same path relative to a config root it owns
(:func:`grove.core.paths.agent_workspace_config_dir`). The read side then
resolves the copy through the ordinary :class:`~grove.core.workspace.TranscriptContext`,
exactly as a container's transcript is read off its mount.

The invariant that makes offsets a complete sync protocol is that every
replicable transcript is append-only (declared per tool by
``AgentAdapter.transcript_root``): the host's file size IS the cursor, so a
worker reconciles after any reconnect by asking for the size, and a chunk
whose offset is not the current size is answered with that size rather than
applied. Nothing on either side persists a cursor, and no byte is ever written
twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from grove.core import paths


class ChunkRefused(Exception):
    """A chunk the mirror cannot apply; ``size`` is where the file actually ends."""

    def __init__(self, reason: str, *, size: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.size = size


class BadRelativePath(ValueError):
    """A path that could place a file outside the workspace's own root."""


@dataclass(frozen=True, slots=True)
class MirroredFile:
    """One replicated transcript file on the host: its path and current size."""

    path: Path
    size: int


class TranscriptMirror:
    """The host side of one workspace's transcript replication.

    Bound to one workspace's root at construction so no method can be handed a
    path for a stranger's directory, the same shape ``PublicWorkspaceReader``
    takes for the same reason.
    """

    #: The largest chunk accepted per request. A transcript line is a few KB
    #: and a tool result at most a few hundred; a worker that has more to send
    #: sends more chunks. Bounds the body a route allocates before checking it.
    CHUNK_LIMIT: Final = 262_144

    def __init__(self, root: Path) -> None:
        self._root = root

    @classmethod
    def for_workspace(cls, workspace_id: str) -> TranscriptMirror:
        return cls(paths.agent_workspace_config_dir(workspace_id))

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def validate_relative(relative_path: str) -> PurePosixPath:
        """The POSIX path a worker named, refused unless it stays under the root.

        Raises:
            BadRelativePath: absolute, empty, or carrying a ``..`` or a
                backslash. The daemon never trusts a path to place a file.
        """
        posix = PurePosixPath(relative_path)
        if (
            not relative_path
            or posix.is_absolute()
            or "\\" in relative_path
            or any(part in {"", ".", ".."} for part in posix.parts)
        ):
            raise BadRelativePath(f"transcript path {relative_path!r} is not a plain relative path")
        return posix

    def resolve(self, relative_path: str) -> Path:
        """The host path for ``relative_path``, validated (see :meth:`validate_relative`)."""
        return self._root / self.validate_relative(relative_path)

    def size_of(self, relative_path: str) -> int:
        """Bytes on disk for ``relative_path``; ``0`` when nothing has landed yet."""
        try:
            return self.resolve(relative_path).stat().st_size
        except FileNotFoundError:
            return 0

    def append(self, relative_path: str, offset: int, data: bytes) -> MirroredFile:
        """Apply the bytes a worker read at ``offset`` of its own file.

        Only the next bytes are applied. A chunk behind the file (a replay
        after a reconnect) or ahead of it (the worker missed a refusal) is
        refused with the current size, so the worker re-reads from there. Both
        are refused rather than tolerated because the alternative, writing at
        the stated offset, lets a late replay truncate or shadow bytes already
        applied.

        Raises:
            ChunkRefused: ``offset`` is not the file's current size.
            BadRelativePath: see :meth:`validate_relative`.
            OSError: the host could not write (surfaced, never swallowed, so a
                full disk reads as a failed sync rather than a quiet gap).
        """
        target = self.resolve(relative_path)
        size = self.size_of(relative_path)
        if offset != size:
            raise ChunkRefused("offset is not the current size", size=size)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("ab") as handle:
            handle.write(data)
        return MirroredFile(path=target, size=size + len(data))

    def read(self, relative_path: str, offset: int) -> bytes:
        """Up to :attr:`CHUNK_LIMIT` bytes of the copy from ``offset``; ``b""`` past its end.

        The read half, for a fresh workload seeding its home from this copy
        before it resumes the session.

        Raises:
            BadRelativePath: see :meth:`validate_relative`.
        """
        try:
            with self.resolve(relative_path).open("rb") as handle:
                handle.seek(offset)
                return handle.read(self.CHUNK_LIMIT)
        except FileNotFoundError:
            return b""
