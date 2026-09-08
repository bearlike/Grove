"""The owned Claude stream-json mailbox transport.

The child in these tests is a real unbuffered Python subprocess speaking NDJSON.
It makes stdin lifetime, reader concurrency, EOF, and process cleanup observable
without starting a real Claude session or depending on credentials.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from grove.core.agents.native_claude import ClaudeNativeOwner
from grove.core.agents.native_owner import AskRecorder, NativeAnswer


async def _eventually(coro: Any, *, timeout: float = 1.0) -> Any:
    return await asyncio.wait_for(coro, timeout=timeout)


def _command(body: str) -> tuple[str, ...]:
    return (sys.executable, "-u", "-c", body)


@pytest.mark.asyncio
async def test_idle_resume_initializes_without_injecting_a_user_turn(tmp_path: Path) -> None:
    child = r"""
import json, sys
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame['type'] == 'control_request':
        assert frame['request']['subtype'] == 'initialize'
        print(json.dumps({'type':'control_response','response':{
            'subtype':'success','request_id':frame['request_id'],'response':{}}}), flush=True)
    else:
        assert frame['message']['content'] == 'followup'
        print(json.dumps({'type':'user','uuid':frame['uuid']}), flush=True)
"""
    sid = "11223344-1122-3344-5566-112233445566"
    owner = ClaudeNativeOwner((*_command(child), "--resume", sid), tmp_path, timeout_seconds=0.5)
    try:
        assert await owner.start("") == sid
        process = owner.process
        result = await owner.send("mbx_" + "a" * 32, "followup")
        assert result.stage == "delivered"
    finally:
        await owner.close()
    assert process is not None and process.returncode is not None


@pytest.mark.asyncio
async def test_idle_resume_refusal_closes_child_without_unobserved_startup_error(
    tmp_path: Path,
) -> None:
    child = r"""
import json, sys
for raw in sys.stdin:
    frame = json.loads(raw)
    print(json.dumps({'type':'control_response','response':{
        'subtype':'error','request_id':frame['request_id'],'error':'resume refused'}}), flush=True)
"""
    owner = ClaudeNativeOwner(
        (*_command(child), "--resume", "11223344-1122-3344-5566-112233445566"),
        tmp_path,
        timeout_seconds=0.5,
    )
    with pytest.raises(RuntimeError, match="not acknowledged"):
        await owner.start("")
    assert owner.process is None


@pytest.mark.asyncio
async def test_large_split_utf8_frames_do_not_stop_the_reader() -> None:
    child = r"""
import json, sys
print(json.dumps({'type':'system','subtype':'init','session_id':'large'}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    text = '🌲' * 70000
    message = {'type':'assistant','message':{'content':[{'type':'text','text':text}]}}
    reply = json.dumps(message, ensure_ascii=False).encode()
    for start in range(0, len(reply), 8191):
        sys.stdout.buffer.write(reply[start:start+8191])
        sys.stdout.buffer.flush()
    sys.stdout.buffer.write(b'\nnot-json\n')
    print(json.dumps({'type':'user','uuid':frame['uuid']}), flush=True)
"""
    emitted: list[str] = []
    owner = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=2, emit=emitted.append)
    try:
        await owner.start("")
        result = await owner.send("mbx_00000000000000000000000000000001", "large")
        assert result.stage == "delivered"
        assert emitted == ["🌲" * 70000]
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_failed_reader_is_observable_and_close_still_reaps_child() -> None:
    child = r"""
import json, sys
print(json.dumps({'type':'system','subtype':'init','session_id':'broken'}), flush=True)
for raw in sys.stdin:
    message = {'type':'assistant','message':{'content':[{'type':'text','text':'trigger'}]}}
    print(json.dumps(message), flush=True)
"""

    def fail_emit(text: str) -> None:
        raise ValueError("reader callback failed")

    owner = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=2, emit=fail_emit)
    process = None
    try:
        await owner.start("")
        process = owner.process
        pending = asyncio.create_task(owner.send("mbx_00000000000000000000000000000002", "go"))
        with pytest.raises(ValueError, match="reader callback failed"):
            await asyncio.wait_for(owner.wait_closed(), 2)
        assert (await asyncio.wait_for(pending, 1)).stage == "unknown"
    finally:
        await owner.close()
    assert process is not None and process.returncode is not None


@pytest.mark.asyncio
async def test_wait_closed_reports_eof_and_releases_a_pending_control() -> None:
    child = r"""
import json, sys
print(json.dumps({'type':'system','subtype':'init','session_id':'eof'}), flush=True)
sys.stdin.readline()
"""
    owner = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=20)
    try:
        await owner.start("")
        pending = asyncio.create_task(owner.set_model("model"))
        await asyncio.wait_for(owner.wait_closed(), 2)
        assert await asyncio.wait_for(pending, 1) is False
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_cancelling_close_watcher_does_not_cancel_live_reader() -> None:
    child = r"""
import json, sys
print(json.dumps({'type':'system','subtype':'init','session_id':'live'}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    print(json.dumps({'type':'user','uuid':frame['uuid']}), flush=True)
"""
    owner = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=2)
    try:
        await owner.start("")
        watcher = asyncio.create_task(owner.wait_closed())
        await asyncio.sleep(0)
        watcher.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watcher
        assert (
            await owner.send("mbx_00000000000000000000000000000003", "still live")
        ).stage == "delivered"
    finally:
        await owner.close()


@pytest.mark.asyncio
async def test_starts_an_owned_stream_and_pairs_each_replayed_uuid() -> None:
    """A replay is input evidence for precisely the submitted mailbox message."""
    child = r"""
import json, sys
required = [
    "-p", "--input-format", "stream-json", "--output-format", "stream-json",
    "--verbose", "--replay-user-messages", "--permission-prompt-tool", "stdio",
]
assert all(item in sys.argv for item in required), sys.argv
# The stdio prompt tool is the ONE permission flag: it routes a question to the
# owner. No permission MODE is ever set here, and no model is chosen.
assert not any(item in {"--permission-mode", "--model"} for item in sys.argv), sys.argv
print(json.dumps({"type": "system", "subtype": "init", "session_id": "native-session"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame["message"]["content"] == "initial":
        continue
    print(json.dumps({
        "type": "user", "uuid": frame["uuid"], "message": frame["message"],
    }), flush=True)
"""
    emitted: list[str] = []
    transport = ClaudeNativeOwner(
        _command(child), Path.cwd(), timeout_seconds=1, emit=emitted.append
    )

    try:
        assert await transport.start("initial") == "native-session"
        first, second = await asyncio.gather(
            transport.send("mbx_00000000000000000000000000000001", "one"),
            transport.send("mbx_00000000000000000000000000000002", "two"),
        )
    finally:
        await transport.close()

    assert first.stage == "delivered"
    assert second.stage == "delivered"
    assert first.evidence is not None
    assert second.evidence is not None
    assert UUID(first.evidence)
    assert UUID(second.evidence)
    assert first.evidence != second.evidence
    assert emitted == []


@pytest.mark.asyncio
async def test_timeout_after_stdin_write_is_unknown_without_a_retry() -> None:
    """A missing native replay is uncertain, not an invitation to duplicate input."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame["message"]["content"] != "initial":
        print(json.dumps({
            "type": "assistant", "message": {"content": [{"type": "text", "text": "working"}]},
        }), flush=True)
"""
    emitted: list[str] = []
    transport = ClaudeNativeOwner(
        _command(child), Path.cwd(), timeout_seconds=2, emit=emitted.append
    )

    try:
        await transport.start("initial")
        result = await transport.send("mbx_00000000000000000000000000000003", "only once")
    finally:
        await transport.close()

    assert result.stage == "unknown"
    assert result.reason == "transport_unknown"
    assert emitted == ["working"]


@pytest.mark.asyncio
async def test_eof_and_invalid_frames_leave_a_submission_unknown() -> None:
    """A malformed line is ignored and a dead owned child never leaves a waiter hung."""
    child = r"""
import json, sys
print("not-json", flush=True)
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame["message"]["content"] != "initial":
        break
"""
    transport = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=1)

    try:
        await transport.start("initial")
        result = await _eventually(transport.send("mbx_00000000000000000000000000000004", "lost"))
    finally:
        await transport.close()

    assert result.stage == "unknown"
    assert result.reason == "transport_unknown"


@pytest.mark.asyncio
async def test_bounded_pending_rejects_backpressure_and_close_reaps_the_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresponsive owner cannot turn messages into an unbounded in-memory queue."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    pass
"""
    transport = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=1)
    monkeypatch.setattr("grove.core.agents.native_claude._MAX_PENDING", 1)
    await transport.start("initial")
    first = asyncio.create_task(transport.send("mbx_00000000000000000000000000000005", "one"))
    await asyncio.sleep(0)
    blocked = await transport.send("mbx_00000000000000000000000000000006", "two")
    process = transport.process

    await transport.close()

    assert blocked.stage == "rejected"
    assert blocked.reason == "backpressure"
    assert (await first).stage == "unknown"
    assert process is not None
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_control_requests_are_reported_without_auto_allowing_them() -> None:
    """Native approval remains Claude's gate; Grove only reports that it appeared."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame["message"]["content"] != "initial":
        print(json.dumps({
            "type": "control_request", "request_id": "cr0",
            "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {}},
        }), flush=True)
        print(json.dumps({"type": "user", "uuid": frame["uuid"]}), flush=True)
"""
    emitted: list[str] = []
    transport = ClaudeNativeOwner(
        _command(child), Path.cwd(), timeout_seconds=1, emit=emitted.append
    )

    try:
        await transport.start("initial")
        result = await transport.send("mbx_00000000000000000000000000000007", "guarded")
    finally:
        await transport.close()

    assert result.stage == "delivered"
    assert emitted == ["Native Claude control request requires native approval."]


# ─── operator controls ride the stream-json control channel ─────────────────


@pytest.mark.asyncio
async def test_interrupt_and_set_model_are_control_requests_answered_by_request_id() -> None:
    """Each control is one `control_request` whose `control_response` is matched
    by the request_id Grove minted; a `success` is True, an `error` is False and
    surfaces the provider's reason on the pane."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame.get("type") != "control_request":
        continue
    rid = frame["request_id"]; req = frame["request"]
    if req["subtype"] == "interrupt":
        # An unrelated response first: matching is by id, never by arrival order.
        print(json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": "someone-else", "response": {}}}), flush=True)
        print(json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": rid,
            "response": {"still_queued": []}}}), flush=True)
    elif req["subtype"] == "set_model" and req["model"] == "good":
        print(json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": rid}}), flush=True)
    else:
        print(json.dumps({"type": "control_response", "response": {
            "subtype": "error", "request_id": rid,
            "error": "API error: 400 bad model"}}), flush=True)
"""
    emitted: list[str] = []
    transport = ClaudeNativeOwner(
        _command(child), Path.cwd(), timeout_seconds=2, emit=emitted.append
    )
    try:
        await transport.start("initial")
        assert await transport.interrupt() is True
        assert await transport.set_model("good") is True
        assert await transport.set_model("bad") is False
    finally:
        await transport.close()
    assert emitted == ["Native control refused: API error: 400 bad model"]


@pytest.mark.asyncio
async def test_control_on_a_dead_owner_is_false_not_a_hang() -> None:
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
"""
    transport = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=1)
    try:
        await transport.start("initial")
        await asyncio.sleep(0.3)  # the child exits after init
        assert await _eventually(transport.interrupt(), timeout=3) is False
    finally:
        await transport.close()
    assert await transport.set_model("x") is False


# ─── questions: can_use_tool held for a human, answered as structure ────────


@pytest.mark.asyncio
async def test_ask_user_question_is_held_recorded_and_answered_with_labels(tmp_path: Path) -> None:
    """The `can_use_tool` for `AskUserQuestion` is not auto-allowed: it is recorded
    as the session's standing ask and answered only when a human's plan arrives,
    as `updatedInput.answers` keyed by the question's prompt (measured 2.1.270)."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
asked = False
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame.get("type") == "user" and not asked:
        asked = True
        print(json.dumps({"type": "control_request", "request_id": "cr1", "request": {
            "subtype": "can_use_tool", "tool_name": "AskUserQuestion", "tool_use_id": "toolu_q",
            "input": {"questions": [
                {"question": "Pick a color", "header": "Color", "multiSelect": False,
                 "options": [{"label": "Red"}, {"label": "Blue"}]},
                {"question": "Toppings?", "header": "T", "multiSelect": True,
                 "options": [{"label": "Cheese"}, {"label": "Olives"}, {"label": "Ham"}]},
            ]}}}), flush=True)
    if frame.get("type") == "control_response":
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "ANSWERED " + json.dumps(frame["response"])}]}}), flush=True)
"""
    spool = tmp_path / "spool"
    asks = AskRecorder(spool)
    asks.bind("s")
    emitted: list[str] = []
    transport = ClaudeNativeOwner(
        _command(child), Path.cwd(), timeout_seconds=2, emit=emitted.append, asks=asks
    )
    try:
        await transport.start("initial")
        await asyncio.sleep(0.5)
        drops = sorted(spool.glob("*.ask.json"))
        assert len(drops) == 1
        recorded = json.loads(drops[0].read_text())
        assert recorded["session_id"] == "s"
        assert recorded["question"]["tool_use_id"] == "toolu_q"
        assert recorded["question"]["tool_name"] == "AskUserQuestion"
        assert emitted == []  # nothing auto-answered, nothing reported as refused

        assert await transport.answer("nope", ()) is False
        ok = await transport.answer(
            "toolu_q",
            (NativeAnswer(indexes=(1,)), NativeAnswer(indexes=(0, 2), text="extra ham")),
        )
        assert ok is True
        await asyncio.sleep(0.5)
    finally:
        await transport.close()
    [reply] = [e for e in emitted if e.startswith("ANSWERED ")]
    response = json.loads(reply.removeprefix("ANSWERED "))
    assert response["request_id"] == "cr1"
    updated = response["response"]["updatedInput"]
    assert response["response"]["behavior"] == "allow"
    assert updated["answers"] == {"Pick a color": "Blue", "Toppings?": "Cheese, Ham — extra ham"}
    assert updated["questions"][0]["question"] == "Pick a color"  # the input is carried whole
    # The clearing drop follows the answer.
    drops = sorted(spool.glob("*.ask.json"), key=lambda p: p.stat().st_mtime)
    assert json.loads(drops[-1].read_text())["question"] is None
    assert await transport.answer("toolu_q", ()) is False  # answered once


@pytest.mark.asyncio
async def test_other_permission_requests_are_reported_not_decided() -> None:
    """A `can_use_tool` for an ordinary tool is neither allowed nor denied — Grove
    inserts no permission gate — it is reported on the pane like before."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame.get("type") == "user":
        print(json.dumps({"type": "control_request", "request_id": "cr2", "request": {
            "subtype": "can_use_tool", "tool_name": "Bash", "tool_use_id": "toolu_b",
            "input": {"command": "rm -rf /"}}}), flush=True)
        break
"""
    emitted: list[str] = []
    transport = ClaudeNativeOwner(
        _command(child), Path.cwd(), timeout_seconds=2, emit=emitted.append
    )
    try:
        await transport.start("initial")
        await asyncio.sleep(0.5)
        assert await transport.answer("toolu_b", ()) is False
    finally:
        await transport.close()
    assert emitted == ["Native Claude control request requires native approval."]


@pytest.mark.asyncio
async def test_a_result_frame_publishes_cost_ttft_and_duration(tmp_path: Path) -> None:
    """The terminal `result` frame is the one payload carrying the harness's own
    price and time to first token; the owner carries them whole (cost is
    cumulative per session on 2.1.270) and states nothing it did not get."""
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if frame.get("type") == "user":
        print(json.dumps({"type": "result", "subtype": "success", "session_id": "s",
            "total_cost_usd": 0.3375, "ttft_ms": 1770, "duration_ms": 5381,
            "permission_denials": [], "num_turns": 2}), flush=True)
"""
    spool = tmp_path / "spool"
    asks = AskRecorder(spool)
    asks.bind("s")
    transport = ClaudeNativeOwner(_command(child), Path.cwd(), timeout_seconds=2, asks=asks)
    try:
        await transport.start("initial")
        await asyncio.sleep(0.5)
    finally:
        await transport.close()
    [drop] = sorted(spool.glob("*.facts.json"))
    facts = json.loads(drop.read_text())["facts"]
    assert facts == {"cost_usd": 0.3375, "ttft_ms": 1770, "turn_duration_ms": 5381}


@pytest.mark.asyncio
async def test_every_frame_crossing_stdio_is_traced_in_both_directions() -> None:
    """The trace sees what the owner wrote AND what it read, unknown kinds included.

    The pane is the wire log: a frame the owner does not understand is
    exactly the one a person debugging a session needs to see, so the trace
    fires before `_observe` decides anything.
    """
    child = r"""
import json, sys
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s"}), flush=True)
print(json.dumps({"type": "something_new", "n": 1}), flush=True)
for raw in sys.stdin:
    frame = json.loads(raw)
    if "uuid" in frame:
        print(json.dumps({"type": "user", "uuid": frame["uuid"]}), flush=True)
"""
    traced: list[tuple[str, str]] = []
    transport = ClaudeNativeOwner(
        _command(child),
        Path.cwd(),
        timeout_seconds=2,
        trace=lambda direction, frame: traced.append((direction, str(frame.get("type")))),
    )
    try:
        await transport.start("initial")
        result = await transport.send("mbx_00000000000000000000000000000009", "hi")
    finally:
        await transport.close()

    assert result.stage == "delivered"
    assert ("send", "user") in traced
    assert ("recv", "system") in traced
    assert ("recv", "something_new") in traced
    assert traced.count(("send", "user")) == 2  # the initial prompt and the send
