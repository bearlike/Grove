"""HTTP/SSE contract tests for one owned OpenCode server."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from grove.core.agents.native_opencode import OpencodeNativeOwner
from grove.core.agents.native_owner import AskRecorder, NativeAnswer


class _Server(ThreadingHTTPServer):
    """A tiny real HTTP server that records the exact legacy OpenCode contract."""

    requests: list[tuple[str, str, dict[str, object] | None]]
    events: list[dict[str, object]]
    password: str | None
    sessions: list[str]

    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _Handler)
        self.requests = []
        self.events = []
        self.password = None
        self.sessions = []


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_GET(self) -> None:
        if not self._authorized():
            self.send_error(401)
            return
        if self.path == "/config":
            self.server.requests.append(("GET", self.path, None))
            self._json({"model": "litellm/qwen/qwen3.7-max", "default_agent": "build"})
            return
        if self.path != "/event":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for event in self.server.events:
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
        while True:
            time.sleep(0.01)

    def do_POST(self) -> None:
        body = self._body()
        self.server.requests.append(("POST", self.path, body))
        if not self._authorized():
            self.send_error(401)
            return
        if self.path == "/config":
            self._json({"model": "litellm/qwen/qwen3.7-max", "default_agent": "build"})
            return
        if self.path == "/session":
            session_id = f"ses-{len(self.server.sessions) + 1}"
            self.server.sessions.append(session_id)
            self._json({"id": session_id})
            return
        if self.path.endswith("/abort"):
            self._json(True)
            return
        if "/permissions/" in self.path or self.path.startswith("/question/"):
            self._json(True)
            return
        if self.path.endswith("/message"):
            self._json({"info": {"id": "msg-delivered"}, "parts": []})
            return
        self.send_error(404)

    def _authorized(self) -> bool:
        password = self.server.password
        if password is None:
            return True
        expected = base64.b64encode(f"opencode:{password}".encode()).decode()
        return self.headers.get("Authorization") == f"Basic {expected}"

    def _body(self) -> dict[str, object] | None:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length)) if length else None

    def _json(self, body: object) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def opencode_server() -> Iterator[_Server]:
    server = _Server(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _command(server: _Server) -> tuple[str, ...]:
    host, port = server.server_address
    return (
        sys.executable,
        "-u",
        "-c",
        f"print('opencode server listening on http://{host}:{port}', flush=True);"
        "import time; time.sleep(60)",
    )


@pytest.mark.asyncio
async def test_start_creates_server_minted_session_and_send_uses_legacy_prompt_shape(
    opencode_server: _Server, tmp_path: Path
) -> None:
    owner = OpencodeNativeOwner(
        _command(opencode_server),
        tmp_path,
        {},
        timeout_seconds=1,
    )
    try:
        assert await owner.start("") == "ses-1"
        submission = await owner.send("message-1", "GROVE_OPENCODE_PROMPT")

        assert submission.stage == "delivered"
        assert submission.evidence == "msg-delivered"
        assert opencode_server.requests == [
            ("GET", "/config", None),
            ("POST", "/session", None),
            (
                "POST",
                "/session/ses-1/message",
                {
                    "model": {"providerID": "litellm", "modelID": "qwen/qwen3.7-max"},
                    "agent": "build",
                    "parts": [{"type": "text", "text": "GROVE_OPENCODE_PROMPT"}],
                },
            ),
        ]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_env_model_overrides_config_and_malformed_model_is_omitted(
    opencode_server: _Server, tmp_path: Path
) -> None:
    owner = OpencodeNativeOwner(
        _command(opencode_server),
        tmp_path,
        {
            "GROVE_OPENCODE_MODEL": "litellm/deepseek/deepseek-v4-pro",
            "GROVE_OPENCODE_AGENT": "plan",
        },
        timeout_seconds=1,
    )
    try:
        await owner.start("")
        await owner.send("message-1", "GROVE_OPENCODE_ENV_MODEL")
        assert opencode_server.requests[-1][2] == {
            "model": {"providerID": "litellm", "modelID": "deepseek/deepseek-v4-pro"},
            "agent": "plan",
            "parts": [{"type": "text", "text": "GROVE_OPENCODE_ENV_MODEL"}],
        }
    finally:
        await owner.close()

    malformed = OpencodeNativeOwner(
        _command(opencode_server),
        tmp_path,
        {"GROVE_OPENCODE_MODEL": "not-a-provider-model"},
        timeout_seconds=1,
    )
    try:
        await malformed.start("")
        await malformed.send("message-2", "GROVE_OPENCODE_NO_MODEL")
        body = opencode_server.requests[-1][2]
        assert isinstance(body, dict)
        assert "model" not in body
    finally:
        await malformed.close()


@pytest.mark.asyncio
async def test_server_wide_event_stream_filters_foreign_session_and_normalizes_ours(
    opencode_server: _Server, tmp_path: Path
) -> None:
    opencode_server.events = [
        {
            "type": "message.part.updated",
            "properties": {
                "sessionID": "ses-foreign",
                "part": {"type": "text", "text": "foreign prose"},
            },
        },
        {
            "type": "message.part.delta",
            "properties": {
                "sessionID": "ses-1",
                "partID": "part-ours",
                "field": "text",
                "delta": "own prose",
            },
        },
    ]
    emitted: list[str] = []
    owner = OpencodeNativeOwner(
        _command(opencode_server),
        tmp_path,
        {},
        emit=emitted.append,
        timeout_seconds=1,
    )
    try:
        await owner.start("")
        for _ in range(50):
            if emitted:
                break
            await asyncio.sleep(0.01)

        assert emitted == ["own prose"]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_interrupt_and_permission_answer_use_legacy_routes(
    opencode_server: _Server, tmp_path: Path
) -> None:
    opencode_server.events = [
        {
            "type": "permission.asked",
            "properties": {
                "sessionID": "ses-1",
                "id": "perm-1",
                "permission": "bash",
                "patterns": ["git status"],
                "metadata": {},
                "always": [],
            },
        }
    ]
    spool = tmp_path / "spool"
    asks = AskRecorder(spool)
    owner = OpencodeNativeOwner(
        _command(opencode_server),
        tmp_path,
        {},
        asks=asks,
        timeout_seconds=1,
    )
    try:
        await owner.start("")
        asks.bind("ses-1")
        for _ in range(50):
            if list(spool.glob("*.ask.json")):
                break
            await asyncio.sleep(0.01)

        assert await owner.interrupt() is True
        assert await owner.answer("perm-1", (NativeAnswer(indexes=(2,)),)) is True
        assert await owner.answer("perm-1", ()) is False
        assert opencode_server.requests[-2:] == [
            ("POST", "/session/ses-1/abort", None),
            (
                "POST",
                "/session/ses-1/permissions/perm-1",
                {"response": "reject"},
            ),
        ]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_question_answer_sends_selected_labels_in_provider_order(
    opencode_server: _Server, tmp_path: Path
) -> None:
    opencode_server.events = [
        {
            "type": "question.asked",
            "properties": {
                "sessionID": "ses-1",
                "id": "que-1",
                "questions": [
                    {
                        "header": "Color",
                        "question": "Pick one",
                        "options": [
                            {"label": "Red", "description": "r"},
                            {"label": "Blue", "description": "b"},
                        ],
                    }
                ],
            },
        }
    ]
    owner = OpencodeNativeOwner(_command(opencode_server), tmp_path, {}, timeout_seconds=1)
    try:
        await owner.start("")
        for _ in range(50):
            if owner._asked:
                break
            await asyncio.sleep(0.01)

        assert await owner.answer("que-1", (NativeAnswer(indexes=(1,), text="note"),)) is True
        assert opencode_server.requests[-1] == (
            "POST",
            "/question/que-1/reply",
            {"answers": [["Blue", "note"]]},
        )
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_close_reaps_the_server_child(opencode_server: _Server, tmp_path: Path) -> None:
    owner = OpencodeNativeOwner(
        _command(opencode_server),
        tmp_path,
        {},
        timeout_seconds=1,
    )
    await owner.start("")
    process = owner._process
    await owner.close()

    assert process is not None and process.returncode is not None
