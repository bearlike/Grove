"""Dictation and read-aloud routes: transport and contract only, against a fake provider."""

from __future__ import annotations

import base64
from collections.abc import Iterator

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core.config import SpeechConfig
from grove.core.speech import OpenAISpeech
from grove.daemon.speech import SpeechRouter

# A non-default variable name, so a test passes only if the configured name is read.
KEY = {"GATEWAY_SPEECH_KEY": "sk-test", "OPENAI_API_KEY": "sk-wrong"}


class _Provider:
    """Records what reached the provider and answers like an OpenAI-compatible one."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        if request.url.path.endswith("/audio/transcriptions"):
            return httpx.Response(200, json={"text": " The build finished. "})
        return httpx.Response(200, content=b"RIFFwav", headers={"content-type": "audio/wav"})


def _client(provider: _Provider, env: dict[str, str] = KEY) -> TestClient:
    speech = OpenAISpeech(
        SpeechConfig(
            base_url="https://gateway.example/v1",
            api_key_env="GATEWAY_SPEECH_KEY",
            stt_model="whisper-large-v3-turbo",
            tts_model="tts-x",
            voice="Kore",
        ),
        env=env,
        transport=httpx.MockTransport(provider),
    )
    app = FastAPI()
    app.include_router(SpeechRouter(speech, auth_dep=lambda: None).router())
    return TestClient(app)


@pytest.fixture
def provider() -> Iterator[_Provider]:
    yield _Provider()


def _recording(data: bytes = b"\x1aE\xdf\xa3webm") -> dict[str, str]:
    return {"audio_base64": base64.b64encode(data).decode(), "filename": "speech.webm"}


def test_a_recording_is_uploaded_as_multipart_and_the_transcript_returned(
    provider: _Provider,
) -> None:
    response = _client(provider).post("/speech/transcriptions", json=_recording())

    assert response.status_code == 200
    assert response.json() == {"text": "The build finished."}
    (sent,) = provider.requests
    assert str(sent.url) == "https://gateway.example/v1/audio/transcriptions"
    assert sent.headers["authorization"] == "Bearer sk-test"
    body = sent.content
    assert b'name="model"' in body and b"whisper-large-v3-turbo" in body
    assert b'filename="speech.webm"' in body and b"\x1aE\xdf\xa3webm" in body


def test_synthesis_relays_the_providers_audio_and_content_type(provider: _Provider) -> None:
    response = _client(provider).post("/speech/synthesis", json={"text": "Two are blocked."})

    assert response.status_code == 200
    assert response.content == b"RIFFwav"
    assert response.headers["content-type"] == "audio/wav"
    (sent,) = provider.requests
    assert sent.url.path == "/v1/audio/speech"
    assert sent.read() == b'{"model":"tts-x","voice":"Kore","input":"Two are blocked."}'


def test_without_a_key_both_routes_refuse_and_nothing_is_sent(provider: _Provider) -> None:
    # OPENAI_API_KEY is set but is not the configured variable, so it must not count.
    client = _client(provider, env={"OPENAI_API_KEY": "sk-wrong"})

    for path, body in (
        ("/speech/transcriptions", _recording()),
        ("/speech/synthesis", {"text": "x"}),
    ):
        response = client.post(path, json=body)
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "speech_unavailable"
        assert "GATEWAY_SPEECH_KEY" in response.json()["detail"]["message"]
    assert provider.requests == []


def test_a_provider_error_is_a_502_not_an_empty_transcript() -> None:
    response = _client(_Provider(status=500)).post("/speech/transcriptions", json=_recording())

    assert response.status_code == 502
    assert response.json()["detail"]["error"] == "speech_provider_failed"


def test_an_oversized_recording_is_refused_before_it_reaches_the_provider(
    provider: _Provider,
) -> None:
    big = _recording(b"\0" * (SpeechRouter.MAX_AUDIO_BYTES + 1))

    response = _client(provider).post("/speech/transcriptions", json=big)

    assert response.status_code == 413
    assert provider.requests == []


@pytest.mark.parametrize(
    "body",
    [
        {"audio_base64": "not base64!", "filename": "speech.webm"},
        {"audio_base64": "AAAA", "filename": "../etc/passwd"},
    ],
)
def test_malformed_input_is_refused(provider: _Provider, body: dict[str, str]) -> None:
    assert _client(provider).post("/speech/transcriptions", json=body).status_code == 422
    assert provider.requests == []


def test_text_over_the_providers_limit_is_refused(provider: _Provider) -> None:
    response = _client(provider).post("/speech/synthesis", json={"text": "x" * 4097})
    assert response.status_code == 422
