"""An owned Claude Code stream-json inbox for one mailbox recipient.

This transport starts a separate ``claude -p`` process.  It never attaches to an
interactive Claude UI, changes its permission/model settings, or decides an
approval: Claude remains the native owner of each turn.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final
from uuid import UUID, uuid4

from grove.core.agents.model import AgentQuestion
from grove.core.agents.native_owner import (
    AskRecorder,
    FrameTrace,
    NativeAnswer,
    NativeSubmission,
)
from grove.core.agents.native_stream import NativeStream
from grove.core.agents.result_frame import parse_result_frame

_MAX_PENDING: Final = 64
_CONTROL_NOTICE: Final = "Native Claude control request requires native approval."
# `--permission-prompt-tool stdio` is what makes a question REACH the owner:
# without it a headless session has nowhere to route `AskUserQuestion` and the
# model is left hunting for a tool it cannot call (measured 2.1.270). Under
# `--dangerously-skip-permissions` ordinary tool calls never arrive here; only
# a question does, which is the one prompt that has no bypass.
_STREAM_FLAGS: Final[tuple[str, ...]] = (
    "-p",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--replay-user-messages",
    "--permission-prompt-tool",
    "stdio",
)


class ClaudeNativeOwner:
    """Own one persistent Claude stream and observe native replay acknowledgements.

    A replayed ``user`` frame proves only that Claude accepted this input on its
    stdin; it does not prove a model read, followed, or completed the message.
    A lost replay remains ``unknown`` after the write, so callers never retry an
    input that may already be queued natively.
    """

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
        self._command = tuple(command)
        self._cwd = cwd
        self._env = dict(env) if env is not None else None
        self._timeout_seconds = timeout_seconds
        self._emit = emit
        self._asks = asks
        self._trace = trace
        # A `can_use_tool` the owner has not answered yet, keyed by the
        # provider's tool_use_id (the address every question surface uses);
        # the value is the frame's request_id plus its input.
        self._asked: dict[str, tuple[str, dict[str, Any]]] = {}
        self._process: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._init: asyncio.Future[str] | None = None
        self._pending: dict[str, asyncio.Future[None]] = {}
        # Outstanding `control_request`s by request_id; the matching
        # `control_response` resolves each with the raw response frame.
        self._controls: dict[str, asyncio.Future[dict[str, Any]]] = {}
        # Context is queried off the control channel, not inferred from terminal
        # result usage (which is cumulative across the session). Each root result
        # advances this epoch; a slow older answer can never overwrite a newer
        # turn or a post-compaction reading.
        self._context_epoch = 0
        self._context_task: asyncio.Task[None] | None = None
        self._session_id: str | None = None
        self._write_lock = asyncio.Lock()
        self._closed = False

    @property
    def process(self) -> asyncio.subprocess.Process | None:
        """The owned child, exposed for lifecycle observation only."""
        return self._process

    async def start(self, initial_prompt: str) -> str:
        """Start the owner, submit its first prompt, then await native init."""
        if self._process is not None:
            raise RuntimeError("Claude mailbox transport has already started")
        if not self._command:
            raise ValueError("Claude mailbox command must not be empty")

        self._init = asyncio.get_running_loop().create_future()
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            *_STREAM_FLAGS,
            cwd=self._cwd,
            env=self._env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self._reader = asyncio.create_task(self._read(), name="grove-claude-mailbox")
        try:
            if initial_prompt:
                await self._write(self._input_frame(initial_prompt))
            elif "--resume" in self._command:
                # Idle resumes emit no system/init until a user turn. Initialize
                # the control channel instead, without inventing a conversation.
                index = self._command.index("--resume")
                resumed_id = str(UUID(self._command[index + 1]))
                if await self._control({"subtype": "initialize"}) is None:
                    raise RuntimeError("native resume initialization was not acknowledged")
                self._session_id = resumed_id
                if self._asks is not None:
                    self._asks.bind(resumed_id)
                self._request_context()
                if not self._init.done():
                    self._init.set_result(resumed_id)
            return await asyncio.wait_for(asyncio.shield(self._init), self._timeout_seconds)
        except BaseException:
            # Cancel the startup waiter before reader cleanup can settle it with
            # an exception nobody will await after this failure/cancellation.
            self._init.cancel()
            if self._init.done() and not self._init.cancelled():
                self._init.exception()
            await self.close()
            raise

    async def send(self, message_id: str, text: str) -> NativeSubmission:
        """Submit one mailbox message and await its exact native replay UUID."""
        process = self._process
        if process is None or self._closed or process.returncode is not None:
            return NativeSubmission(stage="unknown", reason="transport_unknown")

        native_id = self._native_id(message_id)
        async with self._write_lock:
            if len(self._pending) >= _MAX_PENDING:
                return NativeSubmission(stage="rejected", reason="backpressure")
            replayed = asyncio.get_running_loop().create_future()
            self._pending[native_id] = replayed
            try:
                await self._write(self._input_frame(text, native_id))
            except (BrokenPipeError, ConnectionError, RuntimeError, ValueError):
                self._pending.pop(native_id, None)
                return NativeSubmission(stage="unknown", reason="transport_unknown")

        try:
            await asyncio.wait_for(asyncio.shield(replayed), self._timeout_seconds)
        except (TimeoutError, ConnectionError):
            self._pending.pop(native_id, None)
            return NativeSubmission(stage="unknown", reason="transport_unknown")
        return NativeSubmission(stage="delivered", evidence=native_id)

    async def interrupt(self) -> bool:
        """Abort the running turn; ``True`` once Claude acknowledges the request.

        Measured on 2.1.270: the acknowledgement is ``{"still_queued": []}`` and
        a mid-call ``Bash`` ends with its tool_result rejected, so an ack is the
        interrupt having landed, not merely having been read.
        """
        return await self._control({"subtype": "interrupt"}) is not None

    async def set_model(self, model: str) -> bool:
        """Switch the session's model; ``False`` when the provider refused the id.

        Claude validates the id against the API before answering (a bad id on
        2.1.270 comes back as a ``control_response`` error carrying the API's
        400), so a ``True`` means the next turn runs on ``model``.
        """
        return await self._control({"subtype": "set_model", "model": model}) is not None

    async def compact(self) -> bool:
        """Claude stream-json has no provider-native compaction control."""
        return False

    async def invoke_control(self, name: str) -> bool:
        """Claude stream-json exposes no provider-native named-command control."""
        del name
        return False

    async def answer(self, tool_use_id: str, answers: tuple[NativeAnswer, ...]) -> bool:
        """Resolve the standing ``can_use_tool`` for ``tool_use_id`` with the answers.

        Claude's answer shape (verified 2.1.270) is the original input plus an
        ``answers`` map keyed by each question's PROMPT text, whose value is the
        chosen label — several labels joined for a multi-select — with any
        free text appended; the ``control_response`` is ``behavior: allow`` on
        the updated input. An id nobody is asking for answers ``False``: the
        human answered elsewhere, or the turn moved on.
        """
        asked = self._asked.pop(tool_use_id, None)
        if asked is None:
            return False
        request_id, tool_input = asked
        questions = AgentQuestion.from_tool_call("AskUserQuestion", tool_input, tool_use_id)
        rendered: dict[str, str] = {}
        for question, answer in zip(questions, answers, strict=False):
            labels = [
                question.options[i].label for i in answer.indexes if i < len(question.options)
            ]
            parts = [", ".join(labels)] if labels else []
            if answer.text:
                parts.append(answer.text)
            rendered[question.prompt] = " — ".join(parts)
        updated = {**tool_input, "answers": rendered}
        try:
            async with self._write_lock:
                await self._write(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request_id,
                            "response": {"behavior": "allow", "updatedInput": updated},
                        },
                    }
                )
        except (BrokenPipeError, ConnectionError, RuntimeError, ValueError):
            return False
        if self._asks is not None:
            self._asks.resolved()
        return True

    async def _control(self, request: dict[str, object]) -> dict[str, Any] | None:
        """One ``control_request`` round trip; ``None`` on refusal or no owner."""
        process = self._process
        if process is None or self._closed or process.returncode is not None:
            return None
        request_id = uuid4().hex
        answered: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._controls[request_id] = answered
        try:
            async with self._write_lock:
                await self._write(
                    {"type": "control_request", "request_id": request_id, "request": request}
                )
            response = await asyncio.wait_for(asyncio.shield(answered), self._timeout_seconds)
        except (TimeoutError, ConnectionError, BrokenPipeError, RuntimeError, ValueError):
            return None
        finally:
            self._controls.pop(request_id, None)
        if response.get("subtype") != "success":
            self._report(f"Native control refused: {response.get('error', 'unknown error')}")
            return None
        inner = response.get("response")
        return inner if isinstance(inner, dict) else {}

    async def wait_closed(self) -> None:
        """Observe reader EOF/failure without cancelling the drain with its watcher."""
        if self._reader is not None:
            await asyncio.shield(self._reader)

    async def close(self) -> None:
        """Cancel the drain and reap only the subprocess this transport started."""
        if self._closed:
            return
        self._closed = True
        self._finish_pending()
        context_task, self._context_task = self._context_task, None
        if context_task is not None:
            context_task.cancel()
            await asyncio.gather(context_task, return_exceptions=True)
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
            # A failed drain must not skip termination of the child it owned.
            await asyncio.gather(reader, return_exceptions=True)
        process = self._process
        if process is None:
            return
        if process.returncode is not None:
            self._process = None
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()
        finally:
            self._process = None

    async def _write(self, frame: dict[str, object]) -> None:
        """Write a complete NDJSON frame while preserving the child's open stdin."""
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise ConnectionError("Claude mailbox process is unavailable")
        process.stdin.write((json.dumps(frame, separators=(",", ":")) + "\n").encode())
        await process.stdin.drain()
        if self._trace is not None:
            self._trace("send", frame)

    async def _read(self) -> None:
        """Drain stdout indefinitely; malformed provider frames lose only themselves."""
        process = self._process
        if process is None or process.stdout is None:  # pragma: no cover - _start establishes both
            return
        try:
            async for raw in NativeStream.lines(process.stdout):
                try:
                    frame = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue
                if self._trace is not None:
                    self._trace("recv", frame)
                self._observe(frame)
        finally:
            self._finish_pending()
            init = self._init
            if init is not None and not init.done():
                init.set_exception(
                    ConnectionError("Claude mailbox process ended before initialization")
                )

    def _observe(self, frame: dict[str, Any]) -> None:
        """Route documented stream facts without assigning a meaning to results."""
        kind = frame.get("type")
        if kind == "system":
            if frame.get("subtype") == "init":
                self._initialized(frame)
        elif kind == "user":
            self._replayed(frame)
        elif kind == "assistant":
            for text in self._assistant_text(frame):
                self._report(text)
            if frame.get("parent_tool_use_id") is None:
                self._request_context()
        elif kind == "result":
            self._result(frame)
        elif kind == "control_response":
            self._controlled(frame)
        elif kind == "control_request":
            self._asked_by_provider(frame)
        elif kind == "permission_request":
            self._report(_CONTROL_NOTICE)

    def _initialized(self, frame: Mapping[str, Any]) -> None:
        session_id = frame.get("session_id")
        init = self._init
        if isinstance(session_id, str) and session_id:
            self._session_id = session_id
            if self._asks is not None:
                # The current-context request follows init, before NativeWorker
                # receives `start()`'s return. Binding here prevents a fast
                # response from being discarded during that hand-off.
                self._asks.bind(session_id)
            if init is not None and not init.done():
                init.set_result(session_id)
            self._request_context()

    def _replayed(self, frame: Mapping[str, Any]) -> None:
        native_id = frame.get("uuid")
        if isinstance(native_id, str):
            replayed = self._pending.pop(native_id, None)
            if replayed is not None and not replayed.done():
                replayed.set_result(None)
                # A new root input invalidates any earlier current-context
                # response. The coalesced control task observes the new epoch
                # and reads again; no timer polls while the turn runs.
                self._request_context()

    def _controlled(self, frame: Mapping[str, Any]) -> None:
        response = frame.get("response")
        if not isinstance(response, dict):
            return
        request_id = response.get("request_id")
        answered = self._controls.get(request_id) if isinstance(request_id, str) else None
        if answered is not None and not answered.done():
            answered.set_result(response)

    def _result(self, frame: Mapping[str, Any]) -> None:
        """Publish the terminal ``result`` frame's facts (cost, TTFT, duration).

        The one payload with the harness's own price and time to first token —
        `result_frame.parse_result_frame` is the projection; this only carries
        what it says. ``total_cost_usd`` is cumulative across the session's
        turns (measured 2.1.270), so it is stated whole, never summed here.
        """
        if self._asks is None:
            return
        facts = parse_result_frame(dict(frame))
        if facts is None:
            return
        fleet = facts.fleet
        self._asks.facts(
            cost_usd=facts.total_cost_usd,
            ttft_ms=facts.ttft_ms,
            turn_duration_ms=facts.duration_ms,
            permission_denials=facts.permission_denials or None,
            subagents_spawned=None if fleet is None else fleet.spawned,
            subagents_completed=None if fleet is None else fleet.completed,
            subagents_failed=None if fleet is None else fleet.failed,
        )
        # Result usage is a cumulative session counter. The control response is
        # the only current-window source, and only the root session may publish it.
        if facts.session_id == self._session_id:
            self._request_context()

    def _request_context(self) -> None:
        """Coalesce one current-context query after init or a root result.

        ``summary`` is Claude Code's local current estimate (not its expensive
        per-category count). It has the right temporal semantics for a meter;
        unsupported or malformed answers deliberately clear it to unknown.
        """
        if self._asks is None or self._closed:
            return
        self._context_epoch += 1
        if self._context_task is None or self._context_task.done():
            self._context_task = asyncio.create_task(
                self._refresh_context(), name="grove-claude-context"
            )

    async def _refresh_context(self) -> None:
        """Publish the newest root context response, then catch up once if needed."""
        while not self._closed:
            epoch = self._context_epoch
            response = await self._control({"subtype": "get_context_usage", "detail": "summary"})
            context = _context_window(response)
            if epoch == self._context_epoch and self._asks is not None:
                if context is None:
                    self._asks.facts(context_state="clear")
                else:
                    self._asks.facts(
                        context_state="native_control",
                        context_size=context[0],
                        context_used=context[1],
                    )
            if epoch == self._context_epoch:
                return

    def _asked_by_provider(self, frame: Mapping[str, Any]) -> None:
        """Hold a ``can_use_tool`` for a question until a human answers it.

        Only `AskUserQuestion` is held: it is the one prompt a headless session
        has no other channel for, and the human's answer IS the tool's result.
        Any other permission-shaped request (a tool call under a non-bypass
        mode) is neither allowed nor denied here — Grove inserts no permission
        gate — so it is reported on the pane and left to the provider's own
        rules, exactly as before.
        """
        request = frame.get("request")
        request_id = frame.get("request_id")
        if not isinstance(request, dict) or not isinstance(request_id, str):
            return
        tool_use_id = request.get("tool_use_id")
        tool_input = request.get("input")
        if (
            request.get("subtype") != "can_use_tool"
            or request.get("tool_name") != "AskUserQuestion"
            or not isinstance(tool_use_id, str)
            or not isinstance(tool_input, dict)
        ):
            self._report(_CONTROL_NOTICE)
            return
        self._asked[tool_use_id] = (request_id, tool_input)
        if self._asks is not None:
            self._asks.asked(tool_use_id, "AskUserQuestion", tool_input)

    def _finish_pending(self) -> None:
        """Unblock every waiter when the sole owner exits or is explicitly closed."""
        pending, self._pending = self._pending, {}
        for replayed in pending.values():
            if not replayed.done():
                replayed.set_exception(ConnectionError("Claude mailbox process ended"))
        controls, self._controls = self._controls, {}
        for answered in controls.values():
            if not answered.done():
                answered.set_exception(ConnectionError("Claude mailbox process ended"))

    def _report(self, text: str) -> None:
        if self._emit is not None:
            self._emit(text)

    @staticmethod
    def _input_frame(text: str, native_id: str | None = None) -> dict[str, object]:
        """The documented stream-json user envelope; UUID is opted in for pairing."""
        frame: dict[str, object] = {
            "type": "user",
            "message": {"role": "user", "content": text},
            "parent_tool_use_id": None,
        }
        if native_id is not None:
            frame["uuid"] = native_id
        return frame

    @staticmethod
    def _native_id(message_id: str) -> str:
        """Preserve a canonical UUID when supplied; otherwise mint one native id."""
        try:
            value = UUID(message_id)
        except ValueError:
            return str(uuid4())
        return str(value)

    @staticmethod
    def _assistant_text(frame: Mapping[str, Any]) -> tuple[str, ...]:
        """Extract visible assistant text without retaining a transcript."""
        message = frame.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return (content,) if content else ()
        if not isinstance(content, list):
            return ()
        return tuple(
            text
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(text := block.get("text"), str)
            and text
        )


def _context_window(response: object) -> tuple[int, int] | None:
    """Read the same occupancy pair Claude presents in its context report.

    Claude's report divides ``totalTokens`` by ``rawMaxTokens``. Its schema
    defaults an absent raw capacity to ``maxTokens``. The summary is a provider
    estimate of current context, not a sum of historical billed requests.
    """
    if not isinstance(response, dict):
        return None
    used = response.get("totalTokens")
    size = response.get("rawMaxTokens", response.get("maxTokens"))
    if not isinstance(used, int) or isinstance(used, bool) or used < 0:
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        return None
    return size, used
