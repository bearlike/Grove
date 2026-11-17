"""HTTP over dictation and read-aloud. The provider edge is ``grove.core.speech``."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Response

from grove.core.contracts.speech import SynthesisRequest, TranscriptionRequest, TranscriptionView
from grove.core.speech import OpenAISpeech, SpeechProviderFailed, SpeechUnavailable


class SpeechRouter:
    """``POST /speech/transcriptions`` and ``POST /speech/synthesis``, behind the bearer."""

    MAX_AUDIO_BYTES = 10 * 1024 * 1024

    def __init__(self, speech: OpenAISpeech, *, auth_dep: Callable[..., object]) -> None:
        self._speech = speech
        self._auth_dep = auth_dep

    @property
    def available(self) -> bool:
        return self._speech.available

    def router(self) -> APIRouter:
        router = APIRouter(prefix="/speech", tags=["speech"])
        auth = Depends(self._auth_dep)

        @router.post("/transcriptions", response_model=TranscriptionView)
        async def transcribe(request: TranscriptionRequest, _: object = auth) -> TranscriptionView:
            try:
                audio = base64.b64decode(request.audio_base64, validate=True)
            except binascii.Error as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "invalid_audio", "message": "audio is not base64"},
                ) from exc
            if len(audio) > self.MAX_AUDIO_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": "audio_too_large",
                        "message": "a recording may be at most 10 MiB",
                    },
                )
            try:
                return TranscriptionView(
                    text=await self._speech.transcribe(audio, request.filename)
                )
            except (SpeechUnavailable, SpeechProviderFailed) as exc:
                raise _refusal(exc) from exc

        @router.post(
            "/synthesis",
            response_class=Response,
            responses={200: {"content": {"audio/mpeg": {}}, "description": "The spoken audio."}},
        )
        async def synthesize(request: SynthesisRequest, _: object = auth) -> Response:
            try:
                audio, media_type = await self._speech.synthesize(request.text)
            except (SpeechUnavailable, SpeechProviderFailed) as exc:
                raise _refusal(exc) from exc
            return Response(content=audio, media_type=media_type)

        return router

    async def aclose(self) -> None:
        await self._speech.aclose()


def _refusal(exc: SpeechUnavailable | SpeechProviderFailed) -> HTTPException:
    # No key is state-shaped (setting it fixes it); a provider failure is upstream.
    if isinstance(exc, SpeechUnavailable):
        return HTTPException(
            status_code=409, detail={"error": "speech_unavailable", "message": str(exc)}
        )
    return HTTPException(
        status_code=502, detail={"error": "speech_provider_failed", "message": str(exc)}
    )


__all__ = ["SpeechRouter"]
