"""Codex mailbox delivery stays inside one explicitly owned app-server child."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from grove.core.agents.native_codex import CodexNativeOwner
from grove.core.agents.native_owner import AskRecorder, NativeAnswer

# How long a test may wait for a frame the fake server sends AFTER the response
# that released `start()`. A real app server notifies in its own time, so an
# `asyncio.sleep(0)` only yields the loop once and observes the notification
# whenever the subprocess happens to have been scheduled first — which is
# scheduling luck, not a contract, and it fails on a loaded host. Generous
# because it is only ever paid on a genuine regression; the happy path returns
# as soon as the predicate holds.
_FRAME_TIMEOUT_SECONDS = 5.0


async def _until(predicate: object, *, timeout: float = _FRAME_TIMEOUT_SECONDS) -> None:
    """Yield to the loop until ``predicate()`` is true, or fail the test.

    The alternative — a fixed sleep long enough to be safe — makes every run pay
    the worst case and still races on a loaded machine.
    """
    assert callable(predicate)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError(f"frame never arrived within {timeout}s")
        await asyncio.sleep(0.01)


_SERVER = r"""
import json
import os
import sys

for raw in sys.stdin:
    if trace := os.environ.get("GROVE_TEST_TRACE"):
        with open(trace, "a") as output:
            output.write(raw)
    request = json.loads(raw)
    if "method" not in request:
        continue  # a client RESPONSE to a server request: recorded, not answered
    method = request["method"]
    ident = request["id"]
    if method == "initialize":
        result = {"ok": True}
    elif method == "thread/start":
        result = {"thread": {"id": "thread-owned", "status": {"type": "idle"}}}
    elif method == "turn/start":
        result = {"turn": {"id": "turn-started", "status": "inProgress"}}
    elif method == "turn/steer":
        result = {"turnId": request["params"]["expectedTurnId"]}
    else:
        result = {}
    if method == "thread/start" and os.environ.get("GROVE_TEST_APPROVAL"):
        print(json.dumps({
            "jsonrpc": "2.0", "id": 77,
            "method": "commandExecution/requestApproval",
            "params": {"threadId": "thread-owned"},
        }), flush=True)
    # Publish the approval before the response that releases start() so the
    # test exercises an observed block, not subprocess scheduling luck.
    print(json.dumps({"jsonrpc": "2.0", "id": ident, "result": result}), flush=True)
    if method == "turn/start" and os.environ.get("GROVE_TEST_ASSISTANT"):
        print(json.dumps({
            "jsonrpc": "2.0",
            "method": "item/completed",
            "params": {"item": {"type": "agentMessage", "text": "native reply"}},
        }), flush=True)
"""


@pytest.mark.asyncio
async def test_large_split_frames_preserve_notifications_and_rpc_replies(tmp_path: Path) -> None:
    server = _SERVER.replace(
        "    # Publish the approval",
        """    if method == "turn/start":
        item = {"type":"agentMessage","text":"🌲"*70000}
        notification = {"method":"item/completed","params":{"item":item}}
        wire = json.dumps(notification, ensure_ascii=False).encode()
        for start in range(0, len(wire), 8191):
            sys.stdout.buffer.write(wire[start:start+8191])
            sys.stdout.buffer.flush()
        sys.stdout.buffer.write(b"\\nnot-json\\n")
    # Publish the approval""",
    )
    emitted: list[str] = []
    owner = CodexNativeOwner(
        command=(sys.executable, "-u", "-c", server),
        cwd=tmp_path,
        timeout_seconds=2,
        emit=emitted.append,
    )
    try:
        await owner.start("")
        result = await owner.send("large-message", "go")
        assert result.stage == "queued"
        assert emitted == ["🌲" * 70000]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_failed_reader_releases_pending_rpc_and_close_reaps_child(tmp_path: Path) -> None:
    server = _SERVER.replace(
        "    # Publish the approval",
        """    if method == "turn/start":
        item = {"type":"agentMessage","text":"fail"}
        print(json.dumps({"method":"item/completed","params":{"item":item}}), flush=True)
    # Publish the approval""",
    )

    def fail_emit(text: str) -> None:
        raise ValueError("reader callback failed")

    owner = CodexNativeOwner(
        command=(sys.executable, "-u", "-c", server),
        cwd=tmp_path,
        timeout_seconds=20,
        emit=fail_emit,
    )
    process = None
    try:
        await owner.start("")
        process = owner._process
        pending = asyncio.create_task(owner.send("failed-message", "go"))
        with pytest.raises(ValueError, match="reader callback failed"):
            await asyncio.wait_for(owner.wait_closed(), 2)
        assert (await asyncio.wait_for(pending, 1)).stage == "unknown"
    finally:
        await owner.close()
    assert process is not None and process.returncode is not None


@pytest.mark.asyncio
async def test_reader_eof_releases_pending_control(tmp_path: Path) -> None:
    server = _SERVER.replace(
        '    method = request["method"]',
        '    method = request["method"]\n    if method == "thread/settings/update": break',
    )
    owner = CodexNativeOwner(
        command=(sys.executable, "-u", "-c", server), cwd=tmp_path, timeout_seconds=20
    )
    try:
        await owner.start("")
        pending = asyncio.create_task(owner.set_model("model"))
        await asyncio.wait_for(owner.wait_closed(), 2)
        assert await asyncio.wait_for(pending, 1) is False
    finally:
        await owner.close()


def _requests(trace: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in trace.read_text().splitlines()]


async def _until(condition: Callable[[], bool], *, timeout: float = 5.0) -> None:
    """Wait for a REAL subprocess's frames to arrive, bounded.

    ``asyncio.sleep(0)`` yields the loop exactly once, which is enough only when
    the child happens to have already written — so a test built on it passes or
    fails with the scheduler rather than with the code. Poll the condition the
    assertion actually depends on instead; a satisfied condition returns
    immediately, so this costs nothing when the frame is already in.
    """
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition():
        if asyncio.get_running_loop().time() >= deadline:
            return  # let the caller's own assertion report the real difference
        await asyncio.sleep(0.01)


@pytest.fixture
def app_server(tmp_path: Path) -> tuple[str, ...]:
    script = tmp_path / "app_server.py"
    script.write_text(_SERVER)
    return (sys.executable, "-u", str(script))


@pytest.mark.asyncio
async def test_start_owns_a_new_thread_then_submits_initial_prompt_without_turn_overrides(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """The owned app server, not a prior interactive session, receives the prompt."""
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=app_server, cwd=tmp_path, env={"GROVE_TEST_TRACE": str(trace)}
    )

    try:
        assert await transport.start("GROVE_CODEX_START_PROBE") == "thread-owned"

        requests = _requests(trace)
        assert [request["method"] for request in requests] == [
            "initialize",
            "thread/start",
            "turn/start",
        ]
        assert requests[1]["params"] == {"cwd": str(tmp_path)}
        assert requests[2]["params"] == {
            "threadId": "thread-owned",
            "input": [{"type": "text", "text": "GROVE_CODEX_START_PROBE"}],
        }
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_send_steers_the_exact_active_turn_with_compare_and_swap(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """A busy owner gets native steer, never a second turn or a terminal paste."""
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=app_server, cwd=tmp_path, env={"GROVE_TEST_TRACE": str(trace)}
    )

    try:
        await transport.start("initial work")

        submission = await transport.send("message-7", "GROVE_CODEX_STEER_PROBE")

        assert submission.stage == "queued"
        assert submission.evidence == "turn-started"
        request = _requests(trace)[-1]
        assert request == {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "turn/steer",
            "params": {
                "threadId": "thread-owned",
                "expectedTurnId": "turn-started",
                "input": [{"type": "text", "text": "GROVE_CODEX_STEER_PROBE"}],
                "clientUserMessageId": "message-7",
            },
        }
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_pending_native_approval_rejects_without_answering_it(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """Mailbox delivery must never become an approval proxy."""
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=app_server,
        cwd=tmp_path,
        env={"GROVE_TEST_TRACE": str(trace), "GROVE_TEST_APPROVAL": "1"},
    )

    try:
        await transport.start("")
        await _until(lambda: len(_requests(trace)) >= 2)

        submission = await transport.send("message-8", "do not submit")

        assert submission.stage == "rejected"
        assert submission.reason == "recipient_blocked"
        assert [request["id"] for request in _requests(trace)] == [1, 2]
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_send_timeout_after_a_write_is_unknown_without_retry(tmp_path: Path) -> None:
    """A lost acknowledgement is not proof that no native side effect occurred."""
    script = tmp_path / "silent_turn.py"
    turn_response = 'result = {"turn": {"id": "turn-started", "status": "inProgress"}}'
    script.write_text(
        _SERVER.replace(
            f'elif method == "turn/start":\n        {turn_response}',
            'elif method == "turn/start":\n        continue',
        )
    )
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=(sys.executable, "-u", str(script)),
        cwd=tmp_path,
        env={"GROVE_TEST_TRACE": str(trace)},
        # The same budget covers `start()`'s handshake, which must SUCCEED, and
        # the send's acknowledgement, which must expire. At 0.05s the handshake
        # was racing Python's own subprocess boot (~30-50ms), so a loaded host
        # failed in `start()` with a TimeoutError — a flake that reads exactly
        # like the send-timeout behaviour under test. The value only has to be
        # short enough that waiting for it is cheap; nothing here is measuring
        # how fast the timeout fires.
        timeout_seconds=0.5,
    )

    try:
        await transport.start("")
        submission = await transport.send("message-9", "GROVE_CODEX_TIMEOUT_PROBE")

        assert submission.stage == "unknown"
        assert submission.reason == "transport_unknown"
        assert [request["method"] for request in _requests(trace)].count("turn/start") == 1
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_emit_receives_native_assistant_text(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """Rendered replies are native events rather than a second transcript parser."""
    emitted: list[str] = []
    transport = CodexNativeOwner(
        command=app_server,
        cwd=tmp_path,
        env={"GROVE_TEST_ASSISTANT": "1"},
        emit=emitted.append,
    )

    try:
        await transport.start("initial work")
        # The reply is a NOTIFICATION the server sends after the `turn/start`
        # response, so it cannot have arrived by the time `start()` returns.
        await _until(lambda: bool(emitted))

        assert emitted == ["native reply"]
    finally:
        await transport.close()


def test_constructor_keeps_the_caller_command_and_environment_immutable(tmp_path: Path) -> None:
    """A mailbox owner inherits its configured provider environment without mutating it."""
    command = ["codex", "--config", 'model="test"']
    env = {"CODEX_HOME": "/profiles/isolated"}

    transport = CodexNativeOwner(command=command, cwd=tmp_path, env=env)

    command.append("mutated")
    env["CODEX_HOME"] = "mutated"
    assert transport.command == ("codex", "--config", 'model="test"')
    assert transport.env == {"CODEX_HOME": "/profiles/isolated"}


# ─── operator controls: turn/interrupt and thread/settings/update ───────────


@pytest.mark.asyncio
async def test_interrupt_names_the_active_turn_and_set_model_updates_thread_settings(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """Interrupt carries the exact `turnId` the owner is tracking (the schema
    requires it), and a model switch is a thread-settings update rather than a
    turn override — so it survives to every later turn."""
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=app_server, cwd=tmp_path, env={"GROVE_TEST_TRACE": str(trace)}
    )
    try:
        await transport.start("initial work")
        assert await transport.interrupt() is True
        assert await transport.set_model("gpt-5.5") is True
        methods = [(r["method"], r["params"]) for r in _requests(trace)[-2:]]
        assert methods == [
            ("turn/interrupt", {"threadId": "thread-owned", "turnId": "turn-started"}),
            ("thread/settings/update", {"threadId": "thread-owned", "model": "gpt-5.5"}),
        ]
    finally:
        await transport.close()


@pytest.mark.asyncio
async def test_interrupt_with_no_active_turn_is_false_and_sends_nothing(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=app_server, cwd=tmp_path, env={"GROVE_TEST_TRACE": str(trace)}
    )
    try:
        await transport.start("")  # no initial prompt → no turn
        before = len(_requests(trace))
        assert await transport.interrupt() is False
        assert len(_requests(trace)) == before
    finally:
        await transport.close()


# ─── questions and approvals: server requests routed to a human ──────────────

_ASKING_SERVER = _SERVER.replace(
    '    if method == "thread/start" and os.environ.get("GROVE_TEST_APPROVAL"):',
    """    if method == "turn/start" and os.environ.get("GROVE_TEST_ASK"):
        print(json.dumps({"jsonrpc": "2.0", "id": 91, "method": "item/tool/requestUserInput",
            "params": {"threadId": "thread-owned", "turnId": "turn-started", "itemId": "item-q",
                       "isBlocking": True, "questions": [
                {"id": "color", "header": "Color", "question": "Pick a color",
                 "options": [{"label": "Red", "description": "r"},
                             {"label": "Blue", "description": "b"}]}]}}),
            flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": 92,
            "method": "item/commandExecution/requestApproval",
            "params": {"threadId": "thread-owned", "turnId": "turn-started", "itemId": "item-cmd",
                       "startedAtMs": 1, "command": "rm -rf build"}}), flush=True)
    if method == "thread/start" and os.environ.get("GROVE_TEST_APPROVAL"):""",
)


@pytest.fixture
def asking_server(tmp_path: Path) -> tuple[str, ...]:
    script = tmp_path / "asking_server.py"
    script.write_text(_ASKING_SERVER)
    return (sys.executable, "-u", str(script))


@pytest.mark.asyncio
async def test_question_and_approval_are_recorded_and_answered_in_the_schema_shapes(
    asking_server: tuple[str, ...], tmp_path: Path
) -> None:
    """`item/tool/requestUserInput` and `item/commandExecution/requestApproval` are
    held as the session's standing asks (an approval normalized to a two-option
    question) and answered as `{answers: {id: {answers: [...]}}}` / `{decision}`."""
    spool = tmp_path / "spool"
    asks = AskRecorder(spool)
    asks.bind("thread-owned")
    trace = tmp_path / "requests.jsonl"
    transport = CodexNativeOwner(
        command=asking_server,
        cwd=tmp_path,
        env={"GROVE_TEST_TRACE": str(trace), "GROVE_TEST_ASK": "1"},
        asks=asks,
    )
    try:
        await transport.start("do work")
        await asyncio.sleep(0.5)
        drops = [json.loads(p.read_text()) for p in sorted(spool.glob("*.ask.json"))]
        by_id = {d["question"]["tool_use_id"]: d["question"] for d in drops if d["question"]}
        assert set(by_id) == {"item-q", "item-cmd"}
        assert by_id["item-q"]["tool_name"] == "request_user_input"
        assert by_id["item-q"]["tool_input"]["questions"][0]["id"] == "color"
        approval = by_id["item-cmd"]["tool_input"]["questions"][0]
        assert approval["question"] == "rm -rf build"
        assert [o["label"] for o in approval["options"]] == ["Approve", "Decline"]
        # A held request blocks peer mail until answered.
        assert (await transport.send("m1", "hi")).reason == "recipient_blocked"

        assert (
            await transport.answer("item-q", (NativeAnswer(indexes=(1,), text="please"),)) is True
        )
        assert await transport.answer("item-cmd", (NativeAnswer(indexes=(1,)),)) is True
        assert await transport.answer("item-cmd", ()) is False
        await asyncio.sleep(0.3)
    finally:
        await transport.close()
    responses = [r for r in _requests(trace) if "result" in r]
    assert {r["id"]: r["result"] for r in responses} == {
        91: {"answers": {"color": {"answers": ["Blue", "please"]}}},
        92: {"decision": "decline"},
    }
    # Both asks resolved → unblocked for peer mail again.
    assert transport._blocked is False


@pytest.mark.asyncio
async def test_a_completed_command_item_publishes_its_exit_code(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """`item/completed` for a `commandExecution` is the only place a shell's exit
    code is joined to the session on ≥ 0.147; the owner states it as a fact."""
    _server = tmp_path / "exit_server.py"
    _server.write_text(
        _SERVER.replace(
            '    if method == "turn/start" and os.environ.get("GROVE_TEST_ASSISTANT"):',
            """    if method == "turn/start" and os.environ.get("GROVE_TEST_EXIT"):
        print(json.dumps({"jsonrpc": "2.0", "method": "item/completed", "params": {
            "threadId": "thread-owned", "turnId": "turn-started",
            "item": {"type": "commandExecution", "id": "exec-1", "command": "false",
                     "status": "failed", "exitCode": 1, "durationMs": 12}}}), flush=True)
    if method == "turn/start" and os.environ.get("GROVE_TEST_ASSISTANT"):""",
        )
    )
    spool = tmp_path / "spool"
    asks = AskRecorder(spool)
    asks.bind("thread-owned")
    transport = CodexNativeOwner(
        command=(sys.executable, "-u", str(_server)),
        cwd=tmp_path,
        env={"GROVE_TEST_EXIT": "1"},
        asks=asks,
    )
    try:
        await transport.start("run false")
        await asyncio.sleep(0.5)
    finally:
        await transport.close()
    [drop] = sorted(spool.glob("*.facts.json"))
    assert json.loads(drop.read_text())["facts"] == {"last_exit_code": 1, "turn_duration_ms": 12}


@pytest.mark.asyncio
async def test_every_json_rpc_frame_is_traced_in_both_directions(
    app_server: tuple[str, ...], tmp_path: Path
) -> None:
    """Requests, responses and notifications all reach the trace, each once."""
    traced: list[tuple[str, str]] = []
    transport = CodexNativeOwner(
        command=app_server,
        cwd=tmp_path,
        env={"GROVE_TEST_ASSISTANT": "1"},
        trace=lambda direction, frame: traced.append(
            (direction, str(frame.get("method") or f"id={frame.get('id')}"))
        ),
    )
    try:
        await transport.start("initial work")
        await _until(lambda: ("recv", "item/completed") in traced)
    finally:
        await transport.close()

    assert traced[:2] == [("send", "initialize"), ("recv", "id=1")]
    assert ("send", "thread/start") in traced
    assert ("send", "turn/start") in traced
    assert ("recv", "item/completed") in traced
