"""Wire shapes for workspace artifacts: a file or a link an agent hands back.

An artifact is a POINTER. The agent keeps the file or the page current by its
own means, and Grove only remembers where it is, so nothing here carries
content. A file's bytes are fetched on demand from the content route.

``ArtifactRef`` is persisted on ``WorkspaceState.artifact_refs`` and crosses
the wire on every authenticated workspace view, the ``TicketRef`` precedent:
this module imports only pydantic, and the engine dataclass imports it under
``TYPE_CHECKING``. It is deliberately absent from ``public.py``, whose
allowlist keeps a host path off a shared page.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ArtifactKind = Literal["link", "file"]
"""What the target is. A diagram is a ``file`` whose name ends ``.drawio``:
the client picks a renderer from the name, so a new viewable type never
widens this set."""

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class ArtifactRef(BaseModel):
    """One attached artifact.

    ``target`` is the URL for a link and the absolute host path for a file.
    ``id`` is derived from kind and target (see :meth:`id_for`), so it is
    stable across attaches and safe as a URL segment. ``title`` is the
    optional human label; ``None`` means the client shows the target's name.
    """

    model_config = _FROZEN

    id: str = Field(pattern=r"^[0-9a-f]{12}$")
    kind: ArtifactKind
    target: str = Field(min_length=1)
    title: str | None = None

    @staticmethod
    def id_for(kind: ArtifactKind, target: str) -> str:
        """The id of the artifact ``(kind, target)`` names.

        Args:
            kind: Link or file.
            target: The URL or absolute path, already normalized.

        Returns:
            Twelve hex characters, the same for the same pair every time.
        """
        return hashlib.sha256(f"{kind}\0{target}".encode()).hexdigest()[:12]


class ArtifactAttachRequest(BaseModel):
    """Body of ``POST /workspaces/{id}/artifacts``.

    ``target`` is whatever a person or an agent typed: an ``http(s)`` URL, or
    a file path (relative paths resolve against the workspace checkout). The
    engine classifies and validates it, so no client re-implements the rule.
    """

    model_config = _FROZEN

    target: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, min_length=1, max_length=200)


__all__ = ["ArtifactAttachRequest", "ArtifactKind", "ArtifactRef"]
