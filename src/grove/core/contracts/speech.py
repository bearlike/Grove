"""Wire shapes for dictation and read-aloud.

Audio crosses base64 in JSON rather than multipart, for the reason attachments
do: the browser reaches the daemon through a BFF proxy that reads request
bodies as text. The synthesized audio comes back as raw bytes, which that proxy
streams through untouched.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: The decoded ceiling is enforced by the route; this one only stops an
#: oversized string being allocated and decoded first (base64 is 4/3 larger).
_ENCODED_CAP = 14 * 1024 * 1024


class TranscriptionRequest(BaseModel):
    """One finished recording to turn into text.

    ``filename`` travels to the provider as the upload's name, whose extension
    is how it recognises the container (``speech.webm``, ``speech.m4a``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    audio_base64: str = Field(min_length=1, max_length=_ENCODED_CAP)
    filename: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")


class TranscriptionView(BaseModel):
    """The provider's transcript, verbatim."""

    model_config = ConfigDict(frozen=True)

    text: str


class SynthesisRequest(BaseModel):
    """Text to read aloud. 4096 characters is the provider's own limit, so a
    longer answer is split by the client and read chunk by chunk."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(min_length=1, max_length=4096)


__all__ = ["SynthesisRequest", "TranscriptionRequest", "TranscriptionView"]
