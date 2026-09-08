"""Bounded, locked persistence for existing draw.io XML files."""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
import secrets
import stat
import sys
import urllib.parse
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from grove.core import paths
from grove.core.attachments import Attachment, AttachmentStore
from grove.core.errors import DiagramConflict, DiagramUnavailable, WorkspaceStateError

MAX_DIAGRAM_BYTES = 5 * 1024 * 1024
MAX_PREVIEW_BYTES = 10 * 1024 * 1024
_PREVIEW_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class DiagramContents:
    """Validated document XML plus the raw authoritative byte revision."""

    xml: str
    revision: str


@dataclass(slots=True)
class _LockedDiagram:
    """A document's filename held below a pinned, no-follow parent directory."""

    path: Path
    parent_fd: int
    name: str


class DiagramFiles:
    """Safely read and conditionally replace one existing contained ``.drawio``.

    A private state-directory lock is keyed by the resolved lexical target, so
    aliases through root-placement workspaces serialize without leaving untracked
    lock files in a worktree. File access itself is relative to pinned directory
    fds; a symlink swap after validation cannot redirect either read or replace.
    """

    def contents(self, target: _LockedDiagram) -> DiagramContents:
        """Read a source file while its caller holds :meth:`locked`."""
        source, _mode = self._read_source(target)
        return DiagramContents(xml=self._normalize_source(source), revision=self._revision(source))

    def replace(
        self, target: _LockedDiagram, *, expected_revision: str, xml: str
    ) -> DiagramContents:
        """Validate and atomically replace a locked file at one exact revision."""
        normalized = self._normalize_source(xml.encode("utf-8"))
        source, mode = self._read_source(target)
        if self._revision(source) != expected_revision:
            raise DiagramConflict(
                "diagram revision is stale; read the current document before updating"
            )
        self._write_atomic(target, normalized, mode)
        written = normalized.encode("utf-8")
        return DiagramContents(xml=normalized, revision=self._revision(written))

    @contextmanager
    def locked(self, worktree: Path, relative_path: str) -> Iterator[_LockedDiagram]:
        """Yield an existing target below a pinned root with its private lock held."""
        root, parent_fd, name = self._open_parent(worktree, relative_path)
        target = _LockedDiagram(path=root / relative_path, parent_fd=parent_fd, name=name)
        try:
            with self._exclusive_lock(target.path):
                self._ensure_regular_target(target)
                yield target
        finally:
            os.close(parent_fd)

    @staticmethod
    def _revision(source: bytes) -> str:
        return hashlib.sha256(source).hexdigest()

    def _open_parent(self, worktree: Path, relative_path: str) -> tuple[Path, int, str]:
        """Open each path component below a real root without following links."""
        self._validate_relative_path(relative_path)
        try:
            root = worktree.resolve(strict=True)
        except OSError as exc:
            raise DiagramUnavailable("workspace worktree is unavailable") from exc
        if not root.is_dir():
            raise DiagramUnavailable("workspace worktree is unavailable")
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            parent_fd = os.open(root, directory_flags)
        except OSError as exc:
            raise DiagramUnavailable("workspace worktree is unavailable") from exc
        try:
            parts = PurePosixPath(relative_path).parts
            for part in parts[:-1]:
                next_fd = os.open(part, directory_flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            return root, parent_fd, parts[-1]
        except FileNotFoundError as exc:
            os.close(parent_fd)
            raise DiagramUnavailable("diagram file is unavailable") from exc
        except OSError as exc:
            os.close(parent_fd)
            raise WorkspaceStateError(
                "diagram path must not traverse a symlink or non-directory"
            ) from exc

    @staticmethod
    def _validate_relative_path(relative_path: str) -> None:
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or relative_path.startswith(("/", "~"))
            or "\\" in relative_path
            or ":" in relative_path
            or any(part in {"", ".", ".."} for part in relative_path.split("/"))
            or path.suffix.lower() != ".drawio"
        ):
            raise WorkspaceStateError("diagram path must be a workspace-relative .drawio file")

    def _ensure_regular_target(self, target: _LockedDiagram) -> None:
        try:
            info = os.stat(target.name, dir_fd=target.parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise DiagramUnavailable("diagram file is unavailable") from exc
        except OSError as exc:
            raise DiagramUnavailable("diagram file is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise WorkspaceStateError("diagram file must not be a symlink")
        if not stat.S_ISREG(info.st_mode):
            raise WorkspaceStateError("diagram file must be a regular file")

    def _read_source(self, target: _LockedDiagram) -> tuple[bytes, int]:
        """Open a pinned target once without following links and return bounded bytes."""
        self._ensure_regular_target(target)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target.name, flags, dir_fd=target.parent_fd)
        except FileNotFoundError as exc:
            raise DiagramUnavailable("diagram file is unavailable") from exc
        except OSError as exc:
            raise WorkspaceStateError("could not open diagram file safely") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise WorkspaceStateError("diagram file must be a regular file")
            # Buffered read handles short syscalls without accepting a prefix
            # as a whole diagram (and still never allocates beyond the bound).
            with os.fdopen(fd, "rb", closefd=False) as source:
                data = source.read(MAX_DIAGRAM_BYTES + 1)
            if len(data) > MAX_DIAGRAM_BYTES:
                raise WorkspaceStateError("diagram source exceeds the 5 MiB limit")
        finally:
            os.close(fd)
        return data, stat.S_IMODE(info.st_mode)

    def _write_atomic(self, target: _LockedDiagram, text: str, mode: int) -> None:
        """Publish through pinned directory fds, unlike private state-file writes.

        ``paths.write_atomic`` reopens ancestors by name. A diagram's ancestors
        are agent-editable, so reusing it here would undo no-follow containment.
        """
        stage = f".{target.name}.{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(stage, flags, mode, dir_fd=target.parent_fd)
            try:
                payload = text.encode("utf-8")
                offset = 0
                while offset < len(payload):
                    offset += os.write(fd, payload[offset:])
                os.fchmod(fd, mode)
            finally:
                os.close(fd)
            # Recheck the old target in the pinned parent immediately before its
            # name is replaced. The source has never been reached by path lookup.
            self._ensure_regular_target(target)
            os.replace(stage, target.name, src_dir_fd=target.parent_fd, dst_dir_fd=target.parent_fd)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(stage, dir_fd=target.parent_fd)
            raise

    @contextmanager
    def _exclusive_lock(self, canonical_target: Path) -> Iterator[None]:
        """Lock a private-state regular inode; refuse symlinks and hard links."""
        lock_root = paths.ensure_dir(paths.user_state_path().parent / "diagram-locks")
        key = hashlib.sha256(os.fsencode(str(canonical_target))).hexdigest()
        lock = lock_root / key
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(lock, flags, 0o600)
        except OSError as exc:
            raise WorkspaceStateError("could not create diagram lock safely") from exc
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise WorkspaceStateError("diagram lock must be one private regular file")
            if sys.platform != "win32":
                import fcntl  # noqa: PLC0415

                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def _normalize_source(self, source: bytes) -> str:
        """Parse safe draw.io XML, expanding compressed page payloads within one budget."""
        if len(source) > MAX_DIAGRAM_BYTES:
            raise WorkspaceStateError("diagram source exceeds the 5 MiB limit")
        try:
            xml = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkspaceStateError("diagram source must be UTF-8 XML") from exc
        root = self._parse_xml(xml, context="diagram")
        if self._local_name(root.tag) != "mxfile":
            raise WorkspaceStateError("diagram XML must have an mxfile root element")
        pages = [node for node in root if self._local_name(node.tag) == "diagram"]
        if not pages:
            raise WorkspaceStateError("diagram XML must contain at least one diagram page")

        normalized_pages = False
        remaining = MAX_DIAGRAM_BYTES
        for page in pages:
            text = page.text.strip() if page.text else ""
            if len(page):
                model = page[0]
                self._validate_model(model)
                continue
            if not text:
                raise WorkspaceStateError("diagram page must contain an mxGraphModel")
            if text.startswith("<"):
                model = self._parse_xml(text, context="diagram page")
                normalized_pages = True
            else:
                model, consumed = self._inflate_page(text, remaining)
                remaining -= consumed
                normalized_pages = True
            self._validate_model(model)
            page.text = None
            page.append(model)
        if not normalized_pages:
            return xml
        normalized = ElementTree.tostring(root, encoding="unicode", short_empty_elements=True)
        if len(normalized.encode("utf-8")) > MAX_DIAGRAM_BYTES:
            raise WorkspaceStateError("inflated diagram exceeds the 5 MiB limit")
        return normalized

    def _inflate_page(self, encoded: str, remaining: int) -> tuple[ElementTree.Element, int]:
        try:
            compressed = base64.b64decode(urllib.parse.unquote_to_bytes(encoded), validate=True)
            inflater = zlib.decompressobj(-zlib.MAX_WBITS)
            inflated = inflater.decompress(compressed, remaining + 1)
            if len(inflated) > remaining or inflater.unconsumed_tail:
                raise WorkspaceStateError("inflated diagram exceeds the 5 MiB limit")
            inflated += inflater.flush(remaining + 1 - len(inflated))
            if len(inflated) > remaining or not inflater.eof:
                raise WorkspaceStateError(
                    "diagram page has invalid or oversized compressed content"
                )
            page_xml = urllib.parse.unquote_to_bytes(inflated).decode("utf-8")
        except (UnicodeDecodeError, ValueError, zlib.error, binascii.Error) as exc:
            raise WorkspaceStateError("diagram page has unsupported compressed content") from exc
        return self._parse_xml(page_xml, context="diagram page"), len(inflated)

    def _parse_xml(self, xml: str, *, context: str) -> ElementTree.Element:
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise WorkspaceStateError(f"{context} XML must not declare DTDs or entities")
        try:
            return ElementTree.fromstring(xml)
        except ElementTree.ParseError as exc:
            raise WorkspaceStateError(f"{context} XML is invalid") from exc

    def _validate_model(self, model: ElementTree.Element) -> None:
        if self._local_name(model.tag) != "mxGraphModel":
            raise WorkspaceStateError("diagram page must contain an mxGraphModel")
        roots = [child for child in model if self._local_name(child.tag) == "root"]
        if len(roots) != 1 or len(roots[0]) < 2:
            raise WorkspaceStateError("diagram page must contain root cells")

    @staticmethod
    def _local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True, slots=True)
class DiagramPreview:
    """A first-page PNG attachment that is valid for one saved document revision."""

    session_id: str
    revision: str
    attachment: Attachment


class DiagramPreviewFiles:
    """Persist and safely resolve revision-fenced first-page PNG attachments.

    The manifest lives beside attachments rather than in ``WorkspaceState``: a
    preview is derived, workspace-scoped state and must not be lost by another
    writer saving the descriptor. Its hashed workspace key prevents root-mode
    workspaces, which share a worktree, from overwriting each other's mapping.
    """

    @staticmethod
    def ensure_root(worktree: Path | str) -> Path:
        """Reject redirected attachment directories before storing or reading previews."""
        root = Path(worktree).resolve(strict=True)
        current = root
        for part in (".grove", "attachments"):
            current /= part
            if current.is_symlink():
                raise WorkspaceStateError("diagram preview directory must not be a symlink")
            if current.exists() and not current.is_dir():
                raise WorkspaceStateError("diagram preview directory must be a directory")
        return current

    def publish(
        self,
        worktree: Path | str,
        workspace_id: str,
        *,
        session_id: str,
        revision: str,
        attachment: Attachment,
    ) -> DiagramPreview:
        """Atomically map an already stored PNG attachment to one document revision."""
        self._resolve_preview_attachment(worktree, attachment.id)
        self._write_manifest(
            worktree,
            workspace_id,
            {"session_id": session_id, "revision": revision, "attachment_id": attachment.id},
        )
        return DiagramPreview(session_id=session_id, revision=revision, attachment=attachment)

    def read(self, worktree: Path | str, workspace_id: str) -> DiagramPreview:
        """Return the stored preview only when both manifest and PNG are safe."""
        manifest = self._read_manifest(worktree, workspace_id)
        attachment = self._resolve_preview_attachment(worktree, manifest["attachment_id"])
        return DiagramPreview(
            session_id=manifest["session_id"], revision=manifest["revision"], attachment=attachment
        )

    @staticmethod
    def read_png(preview: DiagramPreview) -> bytes:
        """Read one stored preview without following a replaceable file link."""
        try:
            info = os.lstat(preview.attachment.path)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise DiagramUnavailable(
                    "diagram preview image is unavailable; wait for a fresh render"
                )
            fd = os.open(preview.attachment.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            raise DiagramUnavailable(
                "diagram preview image is unavailable; wait for a fresh render"
            ) from exc
        try:
            with os.fdopen(fd, "rb", closefd=False) as source:
                png = source.read(MAX_PREVIEW_BYTES + 1)
        finally:
            os.close(fd)
        DiagramPreviewFiles.validate_png(png)
        return png

    @staticmethod
    def validate_png(png: bytes) -> None:
        """Reject non-PNG and oversized browser export bodies before persistence."""
        if len(png) > MAX_PREVIEW_BYTES:
            raise WorkspaceStateError("diagram preview exceeds the 10 MiB limit")
        if not png.startswith(_PREVIEW_SIGNATURE):
            raise WorkspaceStateError("diagram preview must be a PNG image")

    @staticmethod
    def _manifest_path(worktree: Path | str, workspace_id: str) -> Path:
        key = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()
        return AttachmentStore.root(worktree) / f".diagram-preview-{key}.json"

    def _write_manifest(
        self, worktree: Path | str, workspace_id: str, payload: dict[str, str]
    ) -> None:
        path = self._manifest_path(worktree, workspace_id)
        try:
            self.ensure_root(worktree)
            paths.write_atomic(path, json.dumps(payload, separators=(",", ":")))
        except OSError as exc:
            raise DiagramUnavailable(f"could not save diagram preview metadata: {exc}") from exc

    def _read_manifest(self, worktree: Path | str, workspace_id: str) -> dict[str, str]:
        self.ensure_root(worktree)
        path = self._manifest_path(worktree, workspace_id)
        try:
            info = os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise DiagramUnavailable("diagram preview is unavailable; wait for a fresh render")
            if info.st_size > 1024:
                raise DiagramUnavailable(
                    "diagram preview metadata is unavailable; wait for a fresh render"
                )
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as source:
                raw = source.read(1025)
            if len(raw) > 1024:
                raise DiagramUnavailable("diagram preview metadata is oversized")
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiagramUnavailable(
                "diagram preview is unavailable; wait for a fresh render"
            ) from exc
        if (
            not isinstance(value, dict)
            or set(value) != {"session_id", "revision", "attachment_id"}
            or not all(isinstance(item, str) for item in value.values())
            or len(value["session_id"]) != 32
            or len(value["revision"]) != 64
        ):
            raise DiagramUnavailable(
                "diagram preview metadata is unavailable; wait for a fresh render"
            )
        return value

    @staticmethod
    def _resolve_preview_attachment(worktree: Path | str, attachment_id: str) -> Attachment:
        # AttachmentStore validates ids before joining; additionally pin the
        # expected fixed filename and reject a symlinked directory or image.
        attachment = AttachmentStore.resolve(worktree, attachment_id)
        if attachment is None or attachment.name != "diagram-preview.png":
            raise DiagramUnavailable("diagram preview is unavailable; wait for a fresh render")
        try:
            directory_info = os.lstat(attachment.path.parent)
            file_info = os.lstat(attachment.path)
        except OSError as exc:
            raise DiagramUnavailable(
                "diagram preview is unavailable; wait for a fresh render"
            ) from exc
        if (
            not stat.S_ISDIR(directory_info.st_mode)
            or stat.S_ISLNK(directory_info.st_mode)
            or not stat.S_ISREG(file_info.st_mode)
            or stat.S_ISLNK(file_info.st_mode)
        ):
            raise DiagramUnavailable("diagram preview is unavailable; wait for a fresh render")
        return attachment


__all__ = [
    "MAX_DIAGRAM_BYTES",
    "MAX_PREVIEW_BYTES",
    "DiagramContents",
    "DiagramFiles",
    "DiagramPreview",
    "DiagramPreviewFiles",
]
