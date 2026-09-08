"""Wire shapes for message attachments — the upload request and what it returns.

A client uploads a file, gets back an id, and names that id when it sends a
message. Two steps rather than one multipart message body, for two reasons that
both point the same way: the browser's daemon requests already go through a
JSON-only BFF proxy, and an upload that fails should fail on its own rather than
taking a typed message down with it.

**The bytes cross base64 in JSON, deliberately, and the cost is stated rather
than hidden.** Base64 inflates a payload by a third and multipart would not —
but multipart means a new server dependency, a proxy that must stop reading
request bodies as text, and a second content type on a surface that has exactly
one. For files a person attaches to a chat message, a third of not-very-much is
the cheaper side of that trade. :attr:`AttachmentStore.MAX_BYTES` is what keeps
"not very much" true.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Room for base64's 4/3 expansion over the store's own byte ceiling, plus slack
# for padding and whitespace. The real limit is enforced on the DECODED bytes in
# `AttachmentStore.store`; this one only stops an oversized string being
# allocated and decoded before that check can run.
_ENCODED_CAP = 64 * 1024 * 1024


class AttachmentUploadRequest(BaseModel):
    """POST body for storing one file in a workspace's attachment directory.

    ``name`` is the human's own filename and is sanitized engine-side — it
    reaches a filesystem path, so it is never trusted as a path segment here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=_ENCODED_CAP)


class AttachmentView(BaseModel):
    """One stored attachment, as the client and the agent each need it.

    ``id`` is what a later message names; ``path`` is where the AGENT will find
    the file, already translated into its own namespace (a container path for a
    containerized workspace). The client never composes that path — it is shown,
    not used — because only the engine knows which namespace the agent is in.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    path: str


__all__ = ["AttachmentUploadRequest", "AttachmentView"]
