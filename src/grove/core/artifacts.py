"""Turning a typed artifact target into a ref, and a file ref into bytes.

Answers one question: *may this workspace point at that, and what is in it?*
A target is either a link (``http``/``https``) or a file path. Links are only
remembered. Files are the security boundary of the feature, because the
content route hands their bytes to a browser:

- A file must be a regular file whose REAL path sits under an allowed root:
  the workspace checkout, the project root, and the host's configured
  ``artifacts.roots`` (``/tmp`` by default).
- The check runs at attach AND at every read. A path that was fine when
  attached and has since become a symlink out of the roots is refused.
- A read is bounded, so one attached log cannot make the daemon allocate
  without limit.

The reader on the other side is an authenticated dashboard user, and the
writer is an agent that can already read these files itself. So the boundary
guards the BROWSER's reach (only attached files, only under the roots), not
the agent's.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from grove.core.contracts.artifacts import ArtifactRef
from grove.core.errors import ArtifactInvalid, ArtifactUnavailable

MAX_ARTIFACT_BYTES: Final = 32 * 1024 * 1024
"""Per-read ceiling, the same bound an uploaded attachment has."""

_LINK_SCHEMES: Final = frozenset({"http", "https"})


@dataclass(slots=True, frozen=True)
class ArtifactContent:
    """One file artifact's bytes and the name a viewer picks a renderer by."""

    name: str
    data: bytes


class ArtifactFiles:
    """Classify and validate artifact targets against a fixed set of roots.

    Args:
        roots: Directories a file artifact may live under. A root that does
            not exist is ignored, so a config naming a missing directory
            allows nothing rather than failing every attach.
    """

    def __init__(self, roots: tuple[Path, ...]) -> None:
        self._roots = roots

    def resolve(self, target: str, *, base: Path, title: str | None = None) -> ArtifactRef:
        """Build the ref a typed target names.

        Args:
            target: An ``http(s)`` URL or a file path.
            base: Directory a relative path resolves against.
            title: Optional human label stored on the ref.

        Returns:
            A link ref, or a file ref whose target is the absolute path.

        Raises:
            ArtifactInvalid: The link scheme is not http(s), or the path is
                not a regular file under an allowed root.
        """
        raw = target.strip()
        if "://" in raw:
            parts = urlsplit(raw)
            if parts.scheme.lower() not in _LINK_SCHEMES or not parts.hostname:
                raise ArtifactInvalid(f"{raw!r} is not an http(s) link")
            return ArtifactRef(
                id=ArtifactRef.id_for("link", raw), kind="link", target=raw, title=title
            )
        # Lexical only: the stored target is the path as named, and the real
        # path is derived (and re-checked) each time it is used.
        path = Path(os.path.abspath(Path(base) / Path(raw).expanduser()))
        try:
            self._contained(path)
        except ArtifactUnavailable as exc:
            raise ArtifactInvalid(str(exc)) from exc
        return ArtifactRef(
            id=ArtifactRef.id_for("file", str(path)), kind="file", target=str(path), title=title
        )

    def read(self, ref: ArtifactRef) -> ArtifactContent:
        """Read a file artifact's current bytes.

        Args:
            ref: A stored ref of kind ``file``.

        Returns:
            The file's name and bytes as they are on disk now.

        Raises:
            ArtifactInvalid: The ref is a link, or the file is over the limit.
            ArtifactUnavailable: The file is gone, is no longer a regular
                file, or its real path has left the allowed roots.
        """
        if ref.kind != "file":
            raise ArtifactInvalid("a link artifact has no content to read")
        real = self._contained(Path(ref.target))
        # O_NOFOLLOW on the already-resolved path: a final component swapped
        # for a link between the check and the open is refused, not followed.
        try:
            fd = os.open(real, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise ArtifactUnavailable("artifact file is unavailable") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ArtifactUnavailable("artifact file is unavailable")
            with os.fdopen(fd, "rb", closefd=False) as source:
                data = source.read(MAX_ARTIFACT_BYTES + 1)
        finally:
            os.close(fd)
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ArtifactInvalid("artifact file exceeds the 32 MiB limit")
        return ArtifactContent(name=Path(ref.target).name, data=data)

    def _contained(self, path: Path) -> Path:
        """The real path of *path*, proven to be a regular file under a root.

        Raises:
            ArtifactUnavailable: Missing, not a regular file, or outside
                every allowed root once symlinks are resolved.
        """
        try:
            real = path.resolve(strict=True)
        except OSError as exc:
            raise ArtifactUnavailable(f"{path} does not exist") from exc
        if not real.is_file():
            raise ArtifactUnavailable(f"{path} is not a regular file")
        for root in self._roots:
            try:
                # Roots are resolved too: `/tmp` is itself a link on macOS.
                if real.is_relative_to(root.resolve(strict=True)):
                    return real
            except OSError:
                continue
        allowed = ", ".join(str(root) for root in self._roots)
        raise ArtifactUnavailable(f"{path} is outside the allowed directories ({allowed})")


__all__ = ["MAX_ARTIFACT_BYTES", "ArtifactContent", "ArtifactFiles"]
