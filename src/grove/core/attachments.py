"""Files a human attaches to a message — where they land, and how the agent addresses them.

An attachment has to be readable *by the agent*, which is the whole design
constraint. The agent may be a process on this host or a process inside a
container, and those two namespaces share exactly one directory: the worktree,
which a container workspace bind-mounts. So attachments live under the worktree
at ``.grove/attachments/``, next to the phase files, and a container's path for
one is the same re-rooting :meth:`ContainerRuntimeState.container_path` already
does for ``GROVE_PHASE_FILE`` — no new mount, no host temp directory the
container cannot see.

That placement costs one obligation, and it is the same one ``phase.py`` pays:
an untracked file in the worktree makes ``git worktree remove`` refuse, so
``pause`` and ``kill`` would fail on any workspace that ever received an
attachment. :data:`AttachmentStore.EXCLUDES` is what ``GitRepo.ensure_excluded``
must cover, and the store re-asserts it on every write rather than only at
create — a workspace that predates this feature has no such exclude, and its
first attachment is exactly the moment one is needed.

**One file per directory, and the directory's name is the id.** A human may
attach two files called ``screenshot.png`` in one session, and a flat directory
would silently overwrite the first with the second. A per-attachment directory
also means the original filename survives verbatim — the agent sees the name the
human recognizes rather than a mangled one — while the id keeps the addresses
distinct.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# A generated id: hex, so it is a legal path segment on every filesystem and
# needs no escaping anywhere it is later interpolated.
_ID_PATTERN: Final = re.compile(r"^[0-9a-f]{12}$")

# Characters that make a filename a path, a flag, or a hidden file. Replaced
# rather than rejected: the name is a human's, not an identifier, and refusing
# an upload over a colon would be a worse experience than renaming one.
_UNSAFE_NAME: Final = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(slots=True, frozen=True)
class Attachment:
    """One stored attachment: its id, the name the human gave it, and where it is.

    ``path`` is the HOST path. Translating it into the agent's own namespace is
    the manager's job, because only the manager holds the workspace record that
    knows whether there is a container to translate for.
    """

    id: str
    name: str
    path: Path
    size: int
    """Decoded byte count, read at store or resolve time. It rides the prompt's
    attachment row so the transcript can state a size for a sent file without
    a second read of the worktree."""


class AttachmentStore:
    """Write an attachment under a worktree; read one back by id.

    All-classmethod for :class:`~grove.core.phase.PhaseFile`'s reason — the
    state it owns is on disk, so there is nothing per-instance to hold.

    Unlike the phase file this is NOT best-effort: a caller uploading a file is
    performing a transaction and an unwritable worktree must be a loud failure,
    never a silent success that leaves the agent pointed at nothing.
    """

    RELDIR: Final = ".grove/attachments"
    """Worktree-relative root, a sibling of ``.grove/phase`` — one Grove
    directory a user already knows, rather than a second one to learn."""

    EXCLUDES: Final = (RELDIR + "/",)
    """What ``GitRepo.ensure_excluded`` must cover. The trailing slash matters:
    ``info/exclude`` anchors any pattern containing one to the working-tree
    root, which is exactly the scope wanted here."""

    MAX_BYTES: Final = 32 * 1024 * 1024
    """Per-file ceiling. A bound rather than a policy: the body crosses one JSON
    request, so an unbounded upload is a way to make the daemon allocate
    arbitrary memory on a route that answers in the executor."""

    FALLBACK_NAME: Final = "attachment"
    """What a name that sanitizes to nothing becomes. Never an empty segment,
    which would make the path name a directory instead of a file."""

    @classmethod
    def root(cls, worktree: Path | str) -> Path:
        """The attachment root under *worktree*."""
        return Path(worktree) / cls.RELDIR

    @staticmethod
    def safe_name(name: str) -> str:
        """*name* reduced to a safe single path segment, keeping its extension.

        Traversal (``../``), absolute paths and flag-shaped leading dashes all
        collapse here — the value-becomes-syntax class the branch-name guard
        already covers, arriving through an upload form. Only the basename
        survives, because a browser on Windows sends a full path in some
        versions and the directories in it are meaningless on this host.
        """
        base = Path(name.strip()).name
        cleaned = _UNSAFE_NAME.sub("_", base).lstrip(".-")
        return cleaned or AttachmentStore.FALLBACK_NAME

    @classmethod
    def store(cls, worktree: Path | str, name: str, data: bytes) -> Attachment:
        """Write *data* under a fresh id and return where it landed.

        Raises ``ValueError`` for an over-size payload and ``OSError`` for a
        worktree that cannot be written — both loud, per the class contract.
        """
        if len(data) > cls.MAX_BYTES:
            raise ValueError(
                f"attachment is {len(data)} bytes, over the {cls.MAX_BYTES}-byte limit"
            )
        attachment_id = secrets.token_hex(6)
        safe = cls.safe_name(name)
        path = cls.root(worktree) / attachment_id / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return Attachment(id=attachment_id, name=safe, path=path, size=len(data))

    @classmethod
    def resolve(cls, worktree: Path | str, attachment_id: str) -> Attachment | None:
        """The attachment *attachment_id* names, or ``None`` if there is none.

        The id is pattern-checked before it is ever joined onto a path, so a
        caller-supplied string can never escape the root — containment by
        construction rather than by a ``relative_to`` check after the fact.
        ``None`` for an unknown id is the honest answer for a message naming an
        attachment that was never uploaded, or one whose worktree has since been
        removed by ``pause``.
        """
        if not _ID_PATTERN.match(attachment_id):
            return None
        directory = cls.root(worktree) / attachment_id
        try:
            files = sorted(entry for entry in directory.iterdir() if entry.is_file())
        except OSError:
            return None
        if not files:
            return None
        try:
            size = files[0].stat().st_size
        except OSError:
            return None
        return Attachment(id=attachment_id, name=files[0].name, path=files[0], size=size)


__all__ = ["Attachment", "AttachmentStore"]
