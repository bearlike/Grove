"""File-backed diagram collaboration, with explicit revision preconditions."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from grove.core.contracts.attachments import AttachmentView

DiagramRevision = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
DiagramSessionId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class DiagramOpenRequest(BaseModel):
    """Open an existing diagram under the workspace root, never a host path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=1024)

    @field_validator("path")
    @classmethod
    def relative_drawio_path(cls, value: str) -> str:
        parts = value.split("/")
        if (
            value.startswith(("/", "~"))
            or "\\" in value
            or ":" in value
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
            or PurePosixPath(value).suffix.lower() != ".drawio"
        ):
            raise ValueError("path must be a workspace-relative .drawio file without traversal")
        return value


class DiagramSessionView(DiagramOpenRequest):
    """Small persisted descriptor; document bytes never ride workspace events."""

    session_id: DiagramSessionId
    mode: Literal["active", "read_only"]


class DiagramDocumentView(BaseModel):
    """The latest persisted bytes, not an unacknowledged browser draft."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    diagram: DiagramSessionView
    revision: DiagramRevision
    xml: str


class DiagramStopRequest(BaseModel):
    """Fence a stop against both newer content and a reopened collaboration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: DiagramSessionId
    expected_revision: DiagramRevision


class DiagramUpdateRequest(DiagramStopRequest):
    """Replace a diagram only if the version read is still current."""

    # The codec also bounds decoded UTF-8 bytes; this rejects oversized wire
    # strings before parsing or decompression can allocate another document.
    xml: str = Field(min_length=1, max_length=5 * 1024 * 1024)


class DiagramPreviewUploadRequest(BaseModel):
    """One browser-rendered first-page PNG fenced to an acknowledged revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: DiagramSessionId
    expected_revision: DiagramRevision
    # Base64 expansion of a 10 MiB image, rounded up with padding room. The
    # decoded size is enforced by DiagramPreviewFiles before storage.
    content_base64: str = Field(min_length=1, max_length=14 * 1024 * 1024)


class DiagramPreviewView(BaseModel):
    """Fetch-on-demand first-page PNG for the current acknowledged revision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: DiagramSessionId
    revision: DiagramRevision
    page_index: Literal[0] = 0
    attachment: AttachmentView
    mime_type: Literal["image/png"] = "image/png"
    content_base64: str


__all__ = [
    "DiagramDocumentView",
    "DiagramOpenRequest",
    "DiagramPreviewUploadRequest",
    "DiagramPreviewView",
    "DiagramRevision",
    "DiagramSessionId",
    "DiagramSessionView",
    "DiagramStopRequest",
    "DiagramUpdateRequest",
]
