"""Wire shapes for the host-wide diagram gallery.

The gallery is a READ surface over files the engine already knows how to
find: every ``.drawio`` in a known repo's worktrees, attributed to its
workspace and session. A row is metadata; the bytes and the preview PNG are
fetched per item so a listing of a few hundred diagrams stays one small
JSON body.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from grove.core.gallery import GalleryItem


class GalleryItemView(BaseModel):
    """One ``.drawio`` on the host, as far as the engine could attribute it.

    ``id`` is a stable opaque token for the file (a hash of its path — a
    client never sees or sends a path); ``digest`` is the SHA-256 of its
    bytes and the key a preview is cached under, so a client that rendered
    one revision knows the picture is stale when this changes. The
    ``workspace_*`` trio is ``None`` for a file in a worktree Grove does not
    manage, and the ``session_*`` fields are ``None`` when no transcript was
    recorded at that worktree. ``workspace_live`` and ``session_live`` are
    what decide whether *open workspace* or *open session* is the right verb.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    relative_path: str
    repo_root: str
    repo_name: str
    worktree_path: str
    size_bytes: int
    modified_at: datetime
    digest: str
    pages: int | None
    workspace_id: str | None
    workspace_title: str | None
    workspace_branch: str | None
    workspace_live: bool
    session_id: str | None
    session_kind: str | None
    session_cwd: str | None
    session_title: str | None
    session_live: bool
    preview_ready: bool
    """Whether a browser render for ``digest`` is already cached. ``False`` is
    the ordinary state for a file nobody has opened yet — the client renders
    it and posts the PNG back."""

    @classmethod
    def from_item(cls, item: GalleryItem, *, preview_ready: bool) -> GalleryItemView:
        return cls(
            id=item.id,
            name=item.path.name,
            relative_path=item.relative_path,
            repo_root=str(item.repo_root),
            repo_name=item.repo_name,
            worktree_path=str(item.worktree_path),
            size_bytes=item.size_bytes,
            modified_at=item.modified_at,
            digest=item.digest,
            pages=item.pages,
            workspace_id=item.workspace_id,
            workspace_title=item.workspace_title,
            workspace_branch=item.workspace_branch,
            workspace_live=item.workspace_live,
            session_id=item.session_id,
            session_kind=item.session_kind,
            session_cwd=item.session_cwd,
            session_title=item.session_title,
            session_live=item.session_live,
            preview_ready=preview_ready,
        )


class GalleryDocumentView(BaseModel):
    """One diagram's source, fetched on demand for the viewer and the download."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    digest: str
    xml: str


class GalleryPreviewUploadRequest(BaseModel):
    """A first-page PNG the browser rendered for one content digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    digest: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    content_base64: str = Field(min_length=1, max_length=14 * 1024 * 1024)


class GalleryPreviewView(BaseModel):
    """The cached first-page PNG for one content digest."""

    model_config = ConfigDict(frozen=True)

    digest: str
    mime_type: Literal["image/png"] = "image/png"
    content_base64: str


__all__ = [
    "GalleryDocumentView",
    "GalleryItemView",
    "GalleryPreviewUploadRequest",
    "GalleryPreviewView",
]
