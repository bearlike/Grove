"""Native mailbox input for one Codex app-server owner.

This is deliberately a launch-owned stdio conversation, not an attachment to an
interactive Codex TUI.  The server keeps its configured sandbox and approval
policy because every mailbox turn sends only text input and its exact thread id.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from grove import __version__
from grove.core.agents.native_owner import (
    AskRecorder,
    FrameTrace,
    NativeAnswer,
    NativeSubmission,
)
from grove.core.agents.native_stream import NativeStream

_MAX_PENDING_REQUESTS: Final = 64

# Codex asks the owning client three ways, and Grove routes every one to a
# human as a `PendingQuestion` on the SAME sidecar the hook path writes. The
# question tool carries its own `questions[]` (the batch shape `AgentQuestion`
# already normalizes); an approval has none, so it is normalized as a single
# question whose options are the decisions the schema allows — `accept` /
# `decline` — with the command or reason as the prompt. `cancel` and
# `acceptForSession` exist on the wire and are deliberately not offered: the
# first aborts the turn (a human wanting that has `interrupt`), the second
# widens a grant past this one prompt. `permissions/requestApproval` is
# excluded as well: its answer is a granted permission PROFILE, not a decision,
# and Grove will not compose one on a human's behalf.
#
# Measured 2026-09-14 on 0.154.0. The approval round trip is the ordinary
# case: `--yolo` on the argv does NOT reach the app server's thread (its policy
# stays `on-request`; only a `-c approval_policy=…` override moves it), so a
# native Codex workspace asks before every command unless the operator's
# command carries that override. The question tool is the rarer one:
# `request_user_input` is available ONLY under the `plan` collaboration mode
# (`turn/start … collaborationMode`), and in the default mode the model
# answers "unavailable in Default mode" — a provider gate Grove does not
# second-guess; the hold-and-answer path is exercised against the schema.
_APPROVAL_METHODS: Final[dict[str, str]] = {
    "item/commandExecution/requestApproval": "command",
    "item/fileChange/requestApproval": "file change",
}
_QUESTION_METHOD: Final = "item/tool/requestUserInput"
_APPROVAL_DECISIONS: Final[tuple[str, ...]] = ("accept", "decline")


class CodexNativeOwner:
    """Own one app-server child and submit only to its newly-created top-level thread."""

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
        # Server requests awaiting a human, keyed by the item id every question
        # surface addresses; the value is the JSON-RPC id to answer plus the
        # method, which decides the response shape.
        self._asked: dict[str, tuple[int, str, dict[str, Any]]] = {}
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._requests: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._request_id = 0
        self._thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._blocked = False
        self._lock = asyncio.Lock()

    async def start(self, initial_prompt: str) -> str:
        """Start a new owned thread, then submit its optional first prompt.

        `thread/resume` is intentionally absent: a mailbox may never cold-load or
        attach to a thread an operator did not explicitly register as its owner.
        """
        if self._thread_id is not None:
            return self._thread_id
        await self._open()
        initialized = await self._request(
            "initialize",
            {
                "clientInfo": {"name": "grove", "title": "Grove", "version": __version__},
                "capabilities": {"experimentalApi": True},
            },
        )
        if initialized is None:
            raise RuntimeError("Codex app server did not initialize")
        started = await self._request("thread/start", {"cwd": str(self.cwd)})
        thread = started.get("thread") if isinstance(started, dict) else None
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("Codex app server did not return a thread id")
        self._thread_id = thread_id
        if initial_prompt:
            response = await self._request("turn/start", self._turn_start_params(initial_prompt))
            self._remember_turn(response)
        return thread_id

    async def send(self, message_id: str, text: str) -> NativeSubmission:
        """Submit one mailbox message without changing native execution policy."""
        if self._thread_id is None:
            raise RuntimeError("Codex mailbox owner has not started")
        if self._blocked:
            return NativeSubmission(stage="rejected", reason="recipient_blocked")
        try:
            async with self._lock:
                if self._active_turn_id is None:
                    response = await self._request(
                        "turn/start", self._turn_start_params(text, message_id)
                    )
                    evidence = self._remember_turn(response)
                else:
                    response = await self._request(
                        "turn/steer",
                        {
                            "threadId": self._thread_id,
                            "expectedTurnId": self._active_turn_id,
                            "input": [{"type": "text", "text": text}],
                            "clientUserMessageId": message_id,
                        },
                    )
                    evidence = self._steered_turn(response)
        except TimeoutError:
            # The bytes may have crossed stdin.  Retrying would make two native
            # inputs, so the truthful result is unknown.
            return NativeSubmission(stage="unknown", reason="transport_unknown")
        except RuntimeError:
            return NativeSubmission(stage="unknown", reason="transport_unknown")
        if evidence is None:
            return NativeSubmission(stage="rejected", reason="busy_conflict")
        return NativeSubmission(stage="queued", evidence=evidence)

    async def interrupt(self) -> bool:
        """``turn/interrupt`` the active turn; ``False`` with no turn to stop.

        Measured on 0.154.0: the reply is an empty result and the turn's
        ``turn/completed`` then reports ``status: "interrupted"`` — that
        notification clears ``_active_turn_id`` through the ordinary path.
        """
        if self._thread_id is None or self._active_turn_id is None:
            return False
        params = {"threadId": self._thread_id, "turnId": self._active_turn_id}
        return await self._control("turn/interrupt", params)

    async def set_model(self, model: str) -> bool:
        """``thread/settings/update {model}`` for every subsequent turn.

        The server answers before validating the id against the provider, so a
        ``True`` means the setting is recorded (``thread/settings/updated``
        echoes it), not that the model exists.
        """
        if self._thread_id is None:
            return False
        return await self._control(
            "thread/settings/update", {"threadId": self._thread_id, "model": model}
        )

    async def answer(self, tool_use_id: str, answers: tuple[NativeAnswer, ...]) -> bool:
        """Answer the server request whose item id is ``tool_use_id``.

        A question answers with ``{answers: {<question id>: {answers: [label,
        …]}}}`` (the labels chosen, free text appended as one more entry) —
        the shape the rollout's own ``function_call_output`` records. An
        approval answers ``{decision: accept|decline}`` from the single
        option index. An unknown id is ``False``: nothing is asking.
        """
        asked = self._asked.pop(tool_use_id, None)
        if asked is None:
            return False
        request_id, method, params = asked
        if method == _QUESTION_METHOD:
            result = self._question_answer(params, answers)
        else:
            choice = answers[0].indexes[0] if answers and answers[0].indexes else 1
            decision = _APPROVAL_DECISIONS[min(choice, len(_APPROVAL_DECISIONS) - 1)]
            result = {"decision": decision}
        try:
            await self._respond(request_id, result)
        except RuntimeError:
            return False
        self._blocked = bool(self._asked)
        if self._asks is not None:
            self._asks.resolved()
        return True

    @staticmethod
    def _question_answer(
        params: dict[str, Any], answers: tuple[NativeAnswer, ...]
    ) -> dict[str, Any]:
        questions = params.get("questions")
        rendered: dict[str, dict[str, list[str]]] = {}
        for question, answer in zip(
            questions if isinstance(questions, list) else [], answers, strict=False
        ):
            if not isinstance(question, dict) or not isinstance(question.get("id"), str):
                continue
            options = question.get("options")
            labels = []
            if isinstance(options, list):
                for i in answer.indexes:
                    if i < len(options) and isinstance(options[i], dict):
                        label = options[i].get("label")
                        if isinstance(label, str):
                            labels.append(label)
            if answer.text:
                labels.append(answer.text)
            rendered[question["id"]] = {"answers": labels}
        return {"answers": rendered}

    async def _respond(self, request_id: int, result: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise RuntimeError("Codex app server is not running")
        payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
        await self._send(process, payload)

    async def _send(self, process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
        """Write one frame to the child; the ONE place a sent frame is traced."""
        assert process.stdin is not None
        process.stdin.write((json.dumps(payload) + "\n").encode())
        await process.stdin.drain()
        if self._trace is not None:
            self._trace("send", payload)

    async def _control(self, method: str, params: dict[str, Any]) -> bool:
        try:
            return await self._request(method, params) is not None
        except (TimeoutError, RuntimeError):
            return False

    async def wait_closed(self) -> None:
        """Observe reader EOF/failure without cancelling the drain with its watcher."""
        if self._reader_task is not None:
            await asyncio.shield(self._reader_task)

    async def close(self) -> None:
        """Close the owner and leave no app-server child behind."""
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        process, self._process = self._process, None
        for future in self._requests.values():
            if not future.done():
                future.set_exception(RuntimeError("Codex app server closed"))
        self._requests.clear()
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()

    async def _open(self) -> None:
        if self._process is not None:
            return
        if not self.command:
            raise RuntimeError("Codex mailbox command is empty")
        environment = os.environ.copy()
        if self.env is not None:
            environment.update(self.env)
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            "app-server",
            "--stdio",
            cwd=self.cwd,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader_task = asyncio.create_task(self._read(), name="grove-codex-mailbox")

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise RuntimeError("Codex app server is not running")
        if len(self._requests) >= _MAX_PENDING_REQUESTS:
            raise RuntimeError("Codex app server request capacity reached")
        self._request_id += 1
        request_id = self._request_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._requests[request_id] = future
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            await self._send(process, payload)
            reply = await asyncio.wait_for(future, timeout=self.timeout_seconds)
        finally:
            self._requests.pop(request_id, None)
        if "error" in reply:
            return None
        result = reply.get("result")
        return result if isinstance(result, dict) else None

    async def _read(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            async for line in NativeStream.lines(process.stdout):
                try:
                    frame = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                if self._trace is not None:
                    self._trace("recv", frame)
                frame_id = frame.get("id")
                if isinstance(frame_id, int) and "method" not in frame:
                    future = self._requests.get(frame_id)
                    if future is not None and not future.done():
                        future.set_result(frame)
                    continue
                self._notification(frame)
        finally:
            for future in self._requests.values():
                if not future.done():
                    future.set_exception(RuntimeError("Codex app server closed"))

    def _notification(self, frame: dict[str, Any]) -> None:
        method = frame.get("method")
        if not isinstance(method, str):
            return
        params = frame.get("params")
        params = params if isinstance(params, dict) else {}
        # A server request has an id and requires a client decision. A question
        # or an approval is held for a human and recorded as the session's
        # standing ask; anything else the owner cannot honestly answer stays
        # unanswered, and the owner reports itself blocked for peer mail.
        request_id = frame.get("id")
        if isinstance(request_id, int):
            self._blocked = True
            self._hold(request_id, method, params)
            return
        turn = params.get("turn")
        turn = turn if isinstance(turn, dict) else {}
        turn_id = turn.get("id")
        if method == "turn/started" and isinstance(turn_id, str):
            self._active_turn_id = turn_id
        elif method == "turn/completed" and turn_id == self._active_turn_id:
            self._active_turn_id = None
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                text = item.get("text")
                if isinstance(text, str) and self.emit is not None:
                    self.emit(text)
            # The live stream is the ONLY place a shell's exit code is joined
            # to the session on codex-cli ≥ 0.147: the rollout's
            # `CommandExecution` record carries no link to its tool call.
            # Measured 0.154.0: this item arrives for the `shell` tool; the
            # `exec` (unified_exec / JS REPL) tool the current models prefer
            # runs commands INSIDE a script, so its exit status lives in the
            # tool's output prose and no `commandExecution` item is emitted —
            # reading that prose would be interpreting the tool, so the fact
            # honestly stays absent for those calls.
            if (
                isinstance(item, dict)
                and item.get("type") == "commandExecution"
                and self._asks is not None
            ):
                code = item.get("exitCode")
                duration = item.get("durationMs")
                self._asks.facts(
                    last_exit_code=code
                    if isinstance(code, int) and not isinstance(code, bool)
                    else None,
                    turn_duration_ms=None if not isinstance(duration, int) else duration,
                )

    def _hold(self, request_id: int, method: str, params: dict[str, Any]) -> None:
        item_id = params.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            return
        if method == _QUESTION_METHOD:
            tool_input: dict[str, Any] = {"questions": params.get("questions")}
            tool_name = "request_user_input"
        elif method in _APPROVAL_METHODS:
            what = _APPROVAL_METHODS[method]
            subject = params.get("command") or params.get("reason") or f"Approve this {what}?"
            tool_input = {
                "questions": [
                    {
                        "question": str(subject),
                        "header": f"Approve {what}",
                        "options": [
                            {"label": "Approve", "description": f"Run this {what}."},
                            {"label": "Decline", "description": f"Refuse this {what}."},
                        ],
                    }
                ]
            }
            tool_name = "request_user_input"
        else:
            return
        self._asked[item_id] = (request_id, method, params)
        if self._asks is not None:
            self._asks.asked(item_id, tool_name, tool_input)

    def _turn_start_params(self, text: str, message_id: str | None = None) -> dict[str, Any]:
        assert self._thread_id is not None
        params: dict[str, Any] = {
            "threadId": self._thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if message_id is not None:
            params["clientUserMessageId"] = message_id
        return params

    def _remember_turn(self, response: dict[str, Any] | None) -> str | None:
        turn = response.get("turn") if isinstance(response, dict) else None
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if isinstance(turn_id, str):
            self._active_turn_id = turn_id
            return turn_id
        return None

    def _steered_turn(self, response: dict[str, Any] | None) -> str | None:
        turn_id = response.get("turnId") if isinstance(response, dict) else None
        return turn_id if isinstance(turn_id, str) else None
