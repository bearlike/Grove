"""The speech provider edge: OpenAI-compatible ``/audio/transcriptions`` and ``/audio/speech``.

Transport only. The provider decides the transcript and the audio; this module
sends the bytes, returns what came back, and says plainly when it could not.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx
from loguru import logger

from grove.core.config import SpeechConfig


class SpeechUnavailable(Exception):
    """No provider key in the daemon's environment."""


class SpeechProviderFailed(Exception):
    """The provider answered with an error or did not answer in time."""


class OpenAISpeech:
    """One shared client for every workspace's dictation and read-aloud."""

    TIMEOUT_SECONDS = 60.0

    def __init__(
        self,
        cfg: SpeechConfig,
        *,
        env: Mapping[str, str] = os.environ,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cfg = cfg
        self._key = env.get(cfg.api_key_env, "").strip()
        self._client = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/") + "/",
            timeout=self.TIMEOUT_SECONDS,
            transport=transport,
        )

    @property
    def available(self) -> bool:
        return bool(self._key)

    async def transcribe(self, audio: bytes, filename: str) -> str:
        response = await self._post(
            "audio/transcriptions",
            data={"model": self._cfg.stt_model},
            files={"file": (filename, audio)},
        )
        text = response.json().get("text")
        if not isinstance(text, str):
            raise SpeechProviderFailed("the provider returned no transcript")
        return text.strip()

    async def synthesize(self, text: str) -> tuple[bytes, str]:
        """The audio and its content type, as the provider sent them."""
        response = await self._post(
            "audio/speech",
            json={"model": self._cfg.tts_model, "voice": self._cfg.voice, "input": text},
        )
        return response.content, response.headers.get("content-type", "audio/mpeg")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, **kwargs: object) -> httpx.Response:
        if not self._key:
            raise SpeechUnavailable(
                f"{self._cfg.api_key_env} is not set in the daemon's environment"
            )
        try:
            response = await self._client.post(
                path,
                headers={"Authorization": f"Bearer {self._key}"},
                **kwargs,  # type: ignore[arg-type]
            )
        except httpx.HTTPError as exc:
            logger.warning("speech {} failed: {}", path, type(exc).__name__)
            raise SpeechProviderFailed(
                f"the speech provider did not answer ({type(exc).__name__})"
            ) from exc
        if response.is_error:
            logger.warning("speech {} answered {}", path, response.status_code)
            raise SpeechProviderFailed(f"the speech provider answered {response.status_code}")
        return response


__all__ = ["OpenAISpeech", "SpeechProviderFailed", "SpeechUnavailable"]
