"""One owned OpenCode HTTP server and its server-wide event stream."""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, Final

import httpx

from grove.core.agents.native_owner import (
    AskRecorder,
    FrameTrace,
    NativeAnswer,
    NativeSubmission,
)

_LISTENING_URL: Final = re.compile(r"opencode server listening on (?P<url>https?://\S+)")
_SERVER_TIMEOUT_SECONDS: Final = 3


class OpencodeNativeOwner:
    """Own one OpenCode server and only observe its server-minted session."""

    def __init__(
        self,
        command: Sequence[str],
        cwd: Path,
        env: Mapping[str, str] | None = None,
        timeout_seconds: float = 15,
        emit: Callable[[str], None] | None = None,
        asks: AskRecorder | None = None,
        trace: FrameTrace | None = None,
    ) -> None:
        self.command = tuple(command)
        self.cwd = cwd
        self.env = dict(env) if env is not None else None
        self.timeout_seconds = timeout_seconds
        self.emit = emit
        self._asks = asks
        self._trace = trace
        self._password = secrets.token_urlsafe(32)
        self._process: asyncio.subprocess.Process | None = None
        self._client: httpx.AsyncClient | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._session_id: str | None = None
        self._model: dict[str, str] | None = None
        self._agent: str | None = None
        self._asked: dict[str, str] = {}
        self._question_options: dict[str, tuple[tuple[str, ...], ...]] = {}

    async def start(self, initial_prompt: str) -> str:
        """Launch the server, create its owned session, and send an optional prompt."""
        if self._session_id is not None:
            return self._session_id
        url = await self._open()
        self._client = httpx.AsyncClient(
            base_url=url,
            auth=("opencode", self._password),
            timeout=self.timeout_seconds,
        )
        self._model, self._agent = await self._prompt_defaults()
        response = await self._request("POST", "/session")
        session = self._json(response)
        session_id = session.get("id") if isinstance(session, dict) else None
        if not isinstance(session_id, str) or not session_id:
            await self.close()
            raise RuntimeError("OpenCode server did not return a session id")
        self._session_id = session_id
        if self._asks is not None:
            self._asks.bind(session_id)
        self._reader_task = asyncio.create_task(self._read_events(), name="grove-opencode-events")
        if initial_prompt:
            submission = await self.send("initial", initial_prompt)
            if submission.stage == "rejected":
                await self.close()
                raise RuntimeError("OpenCode server rejected the initial prompt")
        return session_id

    async def send(self, message_id: str, text: str) -> NativeSubmission:
        """Submit one legacy prompt only when its synchronous response proves delivery."""
        del message_id  # OpenCode's legacy prompt endpoint has no caller-message-id field.
        if self._session_id is None:
            raise RuntimeError("OpenCode native owner has not started")
        payload: dict[str, object] = {"parts": [{"type": "text", "text": text}]}
        if self._model is not None:
            payload["model"] = self._model
        if self._agent is not None:
            payload["agent"] = self._agent
        try:
            # `/api/*` accepts custom-provider prompts then fails asynchronously;
            # legacy `/session/{id}/message` is the measured working route.
            response = await self._request("POST", f"/session/{self._session_id}/message", payload)
            body = self._json(response)
        except (httpx.TimeoutException, httpx.TransportError, json.JSONDecodeError):
            # The server may be running the turn despite no response; never retry.
            return NativeSubmission(stage="unknown", reason="transport_unknown")
        except httpx.HTTPStatusError:
            return NativeSubmission(stage="rejected", reason="recipient_blocked")
        info = body.get("info") if isinstance(body, dict) else None
        evidence = info.get("id") if isinstance(info, dict) else None
        return NativeSubmission(
            stage="delivered", evidence=evidence if isinstance(evidence, str) and evidence else None
        )

    async def interrupt(self) -> bool:
        """Abort the owned session's active turn."""
        if self._session_id is None:
            return False
        try:
            response = await self._request("POST", f"/session/{self._session_id}/abort")
            return self._json(response) is True
        except (httpx.HTTPError, RuntimeError):
            return False

    async def set_model(self, model: str) -> bool:
        """Retain OpenCode's exact model for subsequent per-message prompts."""
        parts = self._model_parts(model)
        if parts is None:
            return False
        self._model = parts
        return True

    async def compact(self) -> bool:
        """Request legacy compaction with the retained per-message model."""
        if self._session_id is None or self._model is None:
            return False
        try:
            response = await self._request(
                "POST",
                f"/session/{self._session_id}/summarize",
                {**self._model, "auto": False},
            )
        except (httpx.HTTPError, RuntimeError):
            return False
        return self._json(response) is True

    async def invoke_control(self, name: str) -> bool:
        """Invoke OpenCode's legacy named-command resource for this session."""
        if self._session_id is None:
            return False
        command, separator, arguments = name.strip().lstrip("/").partition(" ")
        if not command:
            return False
        try:
            await self._request(
                "POST",
                f"/session/{self._session_id}/command",
                {"command": command, "arguments": arguments if separator else ""},
            )
        except (httpx.HTTPError, RuntimeError):
            return False
        return True

    async def answer(self, tool_use_id: str, answers: tuple[NativeAnswer, ...]) -> bool:
        """Resolve a held permission or question through its provider-owned route."""
        kind = self._asked.get(tool_use_id)
        if kind is None or self._session_id is None:
            return False
        if kind == "permission":
            index = answers[0].indexes[0] if answers and answers[0].indexes else 2
            if index < 0 or index >= 3:
                return False
            response = ("once", "always", "reject")[index]
            path = f"/session/{self._session_id}/permissions/{tool_use_id}"
            payload: dict[str, object] = {"response": response}
        else:
            expected = len(self._question_options.get(tool_use_id, ()))
            rendered = self._question_answers(tool_use_id, answers)
            if len(rendered) != expected:
                return False
            path = f"/question/{tool_use_id}/reply"
            payload = {"answers": rendered}
        try:
            accepted = self._json(await self._request("POST", path, payload)) is True
        except (httpx.HTTPError, RuntimeError):
            return False
        if not accepted:
            return False
        self._asked.pop(tool_use_id, None)
        self._question_options.pop(tool_use_id, None)
        if self._asks is not None:
            self._asks.resolved()
        return True

    async def wait_closed(self) -> None:
        """Observe the event reader ending without cancelling it."""
        if self._reader_task is not None:
            await asyncio.shield(self._reader_task)

    async def close(self) -> None:
        """Stop the event reader, close HTTP, and reap the server process."""
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()
        process, self._process = self._process, None
        if process is None:
            return
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=_SERVER_TIMEOUT_SECONDS)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def _open(self) -> str:
        if self._process is not None:
            raise RuntimeError("OpenCode native owner has already started")
        if not self.command:
            raise ValueError("OpenCode command must not be empty")
        environment = os.environ.copy()
        if self.env is not None:
            environment.update(self.env)
        environment["OPENCODE_SERVER_PASSWORD"] = self._password
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            "serve",
            "--port",
            "0",
            "--hostname",
            "127.0.0.1",
            cwd=self.cwd,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert self._process.stdout is not None
        try:
            while True:
                line = await asyncio.wait_for(
                    self._process.stdout.readline(), timeout=self.timeout_seconds
                )
                match = _LISTENING_URL.search(line.decode(errors="replace"))
                if match is not None:
                    return match["url"]
                if not line:
                    raise RuntimeError("OpenCode server exited before listening")
        except BaseException:
            await self.close()
            raise

    async def _prompt_defaults(self) -> tuple[dict[str, str] | None, str | None]:
        """Resolve optional per-agent choices before letting the server choose."""
        env = self.env or {}
        configured_model = env.get("GROVE_OPENCODE_MODEL")
        configured_agent = env.get("GROVE_OPENCODE_AGENT")
        try:
            config = self._json(await self._request("GET", "/config"))
        except (httpx.HTTPError, RuntimeError):
            config = {}
        default_model = config.get("model") if isinstance(config, dict) else None
        default_agent = config.get("default_agent") if isinstance(config, dict) else None
        model = self._model_parts(configured_model or default_model)
        agent = configured_agent or default_agent
        return model, agent if isinstance(agent, str) and agent else None

    @staticmethod
    def _model_parts(value: object) -> dict[str, str] | None:
        if not isinstance(value, str):
            return None
        provider, separator, model = value.partition("/")
        if not provider or not separator or not model:
            return None
        return {"providerID": provider, "modelID": model}

    async def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None
    ) -> httpx.Response:
        client = self._client
        if client is None:
            raise RuntimeError("OpenCode HTTP client is unavailable")
        request = client.build_request(method, path, json=payload)
        if self._trace is not None:
            self._trace("send", self._request_trace(request, payload))
        response = await client.send(request)
        response.raise_for_status()
        if self._trace is not None:
            self._trace("recv", {"status": response.status_code, "url": path})
        return response

    async def _read_events(self) -> None:
        client = self._client
        if client is None:
            return
        try:
            async with client.stream("GET", "/event") as response:
                response.raise_for_status()
                async for frame in self._sse_frames(response.aiter_lines()):
                    if self._trace is not None:
                        self._trace("recv", frame)
                    self._observe(frame)
        except (httpx.HTTPError, asyncio.CancelledError):
            return

    def _observe(self, frame: dict[str, Any]) -> None:
        properties = frame.get("properties")
        if not isinstance(properties, dict):
            return
        # `/event` is shared by every session on this server. Dropping another
        # session here prevents its text or permission from reaching this owner.
        if properties.get("sessionID") != self._session_id:
            return
        kind = frame.get("type")
        if kind == "message.part.updated":
            self._updated_text(properties)
        elif kind == "message.part.delta":
            self._text_delta(properties)
        elif kind == "permission.asked":
            self._permission_asked(properties)
        elif kind == "question.asked":
            self._question_asked(properties)

    def _updated_text(self, properties: dict[str, Any]) -> None:
        part = properties.get("part")
        text = part.get("text") if isinstance(part, dict) and part.get("type") == "text" else None
        if isinstance(text, str) and text and self.emit is not None:
            self.emit(text)

    def _text_delta(self, properties: dict[str, Any]) -> None:
        delta = properties.get("delta") if properties.get("field") == "text" else None
        if isinstance(delta, str) and delta and self.emit is not None:
            self.emit(delta)

    def _permission_asked(self, properties: dict[str, Any]) -> None:
        permission_id = properties.get("id")
        if not isinstance(permission_id, str) or not permission_id:
            return
        self._asked[permission_id] = "permission"
        if self._asks is not None:
            self._asks.asked(
                permission_id,
                "request_user_input",
                {
                    "questions": [
                        {
                            "question": properties.get("permission", "Approve this action?"),
                            "header": "Permission",
                            "options": [
                                {"label": "Once", "description": "Approve once."},
                                {"label": "Always", "description": "Always approve."},
                                {"label": "Reject", "description": "Reject this action."},
                            ],
                        }
                    ]
                },
            )

    def _question_asked(self, properties: dict[str, Any]) -> None:
        question_id = properties.get("id")
        raw_questions = properties.get("questions")
        if (
            not isinstance(question_id, str)
            or not question_id
            or not isinstance(raw_questions, list)
        ):
            return
        questions: list[dict[str, Any]] = []
        options: list[tuple[str, ...]] = []
        for raw in raw_questions:
            if not isinstance(raw, dict):
                return
            labels = tuple(
                option["label"]
                for option in raw.get("options", [])
                if isinstance(option, dict) and isinstance(option.get("label"), str)
            )
            if not labels:
                return
            question = dict(raw)
            # The live server names this `multiple`; AgentQuestion's published
            # shape names it `multiSelect`. Normalize only the transport spelling.
            question["multiSelect"] = bool(raw.get("multiple", False))
            questions.append(question)
            options.append(labels)
        self._asked[question_id] = "question"
        self._question_options[question_id] = tuple(options)
        if self._asks is not None:
            self._asks.asked(question_id, "request_user_input", {"questions": questions})

    @staticmethod
    async def _sse_frames(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
        data: list[str] = []
        async for line in lines:
            if line:
                if line.startswith("data:"):
                    data.append(line.removeprefix("data:").lstrip())
                continue
            if not data:
                continue
            raw, data = "\n".join(data), []
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(frame, dict):
                yield frame

    @staticmethod
    def _json(response: httpx.Response) -> Any:
        return response.json()

    @staticmethod
    def _request_trace(request: httpx.Request, payload: dict[str, object] | None) -> dict[str, Any]:
        frame: dict[str, Any] = {"method": request.method, "url": str(request.url)}
        if payload is not None:
            frame["body"] = payload
        return frame

    def _question_answers(
        self, tool_use_id: str, answers: tuple[NativeAnswer, ...]
    ) -> list[list[str]]:
        """Render answers against the CAPTURED options — never popped here.

        The caller (:meth:`answer`) owns removal: it must first compare this
        render's length against the captured question count, which needs the
        entry to still be present, and only clear it once the provider has
        actually accepted the reply.
        """
        options = self._question_options.get(tool_use_id, ())
        rendered: list[list[str]] = []
        for choices, answer in zip(options, answers, strict=False):
            labels = [choices[index] for index in answer.indexes if 0 <= index < len(choices)]
            if answer.text:
                labels.append(answer.text)
            rendered.append(labels)
        return rendered
