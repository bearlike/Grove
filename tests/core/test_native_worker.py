"""An initial task without a native acknowledgment cannot leave a ready worker."""

from __future__ import annotations

import asyncio
import json
import signal

import httpx
import pytest

from grove.core.agents.native_owner import NativeAnswer, NativeSubmission
from grove.core.native_worker import (
    _BOOT_PROMPT,
    _PEER_PREAMBLE,
    NativeWorker,
    NativeWorkerConfig,
    _run,
)


def _once_then_revoked(handler):
    """Serve one stream, then answer 401: the worker must end on a revoked token.

    The worker reconnects on a clean EOF (a daemon restart), so a mock that
    kept serving the same stream would loop forever; a 401 is the one answer
    that ends it, and it is what a killed workspace's worker really sees.
    """
    served = {"n": 0}

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/connection":
            served["n"] += 1
            if served["n"] > 1:
                return httpx.Response(401, json={"detail": "revoked"})
        return handler(request)

    return route


@pytest.mark.asyncio
async def test_initial_rejection_closes_native_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider that REFUSES the task ends the worker; nothing else does."""

    class Native:
        closed = False

        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            return NativeSubmission(stage="rejected")

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            self.closed = True

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text='event: registered\ndata: {"generation":"test"}\n\n',
        )
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(
            base_url="http://localhost",
            transport=transport,
        ),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="do the thing",
    )
    with pytest.raises(RuntimeError, match="rejected"):
        await NativeWorker(config).run()
    assert native.closed


@pytest.mark.asyncio
async def test_an_unechoed_task_keeps_the_session_and_sends_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`unknown` is evidence that the replay was slow, not a refusal.

    The original shape exited on it, which killed the session exactly when
    the provider was busy (a session-start hook, a slow gateway), and the
    pane held a bare `RuntimeError`. The task is sent ONCE across reconnects:
    the child still holds the conversation.
    """

    class Native:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.sent: list[str] = []
            self.closed = False

        async def start(self, prompt: str) -> str:
            self.started.append(prompt)
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.sent.append(text)
            return NativeSubmission(stage="unknown", reason="transport_unknown")

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            self.closed = True

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    real_client = httpx.AsyncClient
    stream = 'event: registered\ndata: {"generation":"test"}\n\n'
    transport = httpx.MockTransport(
        _once_then_revoked(lambda request: httpx.Response(200, text=stream))
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="do the thing",
    )
    await NativeWorker(config).run()
    assert len(native.sent) == 1 and native.sent[0].endswith("do the thing")
    assert native.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["eof", "ack_error", "read_error"])
async def test_initial_task_stays_submitted_when_the_connection_fails_after_send(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A reconnect must not replay a task already handed to the provider."""

    class Native:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.sent: list[str] = []

        async def start(self, prompt: str) -> str:
            self.started.append(prompt)
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.sent.append(text)
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    real_client = httpx.AsyncClient
    connections = {"n": 0}

    class ReadErrorAfterRegistration(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: registered\ndata: {"generation":"first"}\n\n'
            raise httpx.ReadError("daemon restarted")

        async def aclose(self) -> None:
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/ack":
            return httpx.Response(500, json={"detail": "restarting"})
        if request.url.path != "/mailboxes/connection":
            return httpx.Response(404)
        connections["n"] += 1
        if connections["n"] == 1:
            if failure == "read_error":
                return httpx.Response(200, stream=ReadErrorAfterRegistration())
            stream = 'event: registered\ndata: {"generation":"first"}\n\n'
            if failure == "ack_error":
                stream += (
                    'event: delivery\ndata: {"message_id":"mbx_'
                    + "1" * 32
                    + '","text":"peer mail"}\n\n'
                )
            return httpx.Response(200, text=stream)
        if connections["n"] == 2:
            return httpx.Response(200, text='event: registered\ndata: {"generation":"second"}\n\n')
        return httpx.Response(401, json={"detail": "revoked"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="do the thing",
    )

    await NativeWorker(config).run()

    assert native.started == [
        "Initialize this Grove-owned native session. Reply READY only; "
        "do not run tools. The workspace task will arrive after registration."
    ]
    assert len(native.sent) == (2 if failure == "ack_error" else 1)
    assert native.sent[0].endswith("do the thing")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["eof", "error"])
async def test_connection_failure_before_initial_task_leaves_it_unsent(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A failed registration does not claim a task the provider never received."""

    class Native:
        def __init__(self) -> None:
            self.started = 0
            self.sent: list[str] = []

        async def start(self, prompt: str) -> str:
            self.started += 1
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.sent.append(text)
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    real_client = httpx.AsyncClient
    connections = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/mailboxes/connection":
            return httpx.Response(404)
        connections["n"] += 1
        if connections["n"] == 1:
            if failure == "error":
                return httpx.Response(500, json={"detail": "restarting"})
            return httpx.Response(200, text="")
        if connections["n"] == 2:
            return httpx.Response(200, text='event: registered\ndata: {"generation":"second"}\n\n')
        return httpx.Response(401, json={"detail": "revoked"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="do the thing",
    )

    await NativeWorker(config).run()

    assert native.started == 1
    assert len(native.sent) == 1 and native.sent[0].endswith("do the thing")


@pytest.mark.asyncio
async def test_worker_relays_operator_controls_and_never_acks_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`interrupt` / `set_model` / `steer` frames go straight to the owner; only
    peer `message` frames are acknowledged through `/mailboxes/ack`."""

    class Native:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.calls.append(("send", text))
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def interrupt(self) -> bool:
            self.calls.append(("interrupt", ""))
            return True

        async def set_model(self, model: str) -> bool:
            self.calls.append(("set_model", model))
            return True

        async def set_effort(self, effort: str) -> bool:
            self.calls.append(("set_effort", effort))
            return True

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    acks: list[dict[str, object]] = []
    stream = (
        'event: registered\ndata: {"generation":"test"}\n\n'
        'event: delivery\ndata: {"op":"interrupt","message_id":"","text":""}\n\n'
        'event: delivery\ndata: {"op":"set_model","message_id":"","text":"opus"}\n\n'
        'event: delivery\ndata: {"op":"set_effort","message_id":"","text":"xhigh"}\n\n'
        'event: delivery\ndata: {"op":"steer","message_id":"","text":"do this"}\n\n'
        'event: delivery\ndata: {"message_id":"mbx_' + "1" * 32 + '","text":"peer mail"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/ack":
            acks.append(json.loads(request.content))
            return httpx.Response(
                200, json={"stage": "delivered", "created_at": "2026-01-01T00:00:00Z"}
            )
        return httpx.Response(200, text=stream)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(_once_then_revoked(handler))
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="do the thing",
    )
    await NativeWorker(config).run()

    assert [call for call in native.calls if call[0] != "send"] == [
        ("interrupt", ""),
        ("set_model", "opus"),
        ("set_effort", "xhigh"),
    ]
    sent = [text for op, text in native.calls if op == "send"]
    assert sent[0].endswith("do the thing")
    assert sent[1:] == ["do this", "peer mail"]
    assert [ack["message_id"] for ack in acks] == ["mbx_" + "1" * 32]


@pytest.mark.asyncio
async def test_worker_relays_an_answer_frame_as_structured_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `answer` op carries the manager's JSON plan; the worker turns it into
    `NativeAnswer`s and the owner resolves the ask by tool-use id."""

    class Native:
        def __init__(self) -> None:
            self.answered: list[tuple[str, tuple[NativeAnswer, ...]]] = []

        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def interrupt(self) -> bool:
            return True

        async def set_model(self, model: str) -> bool:
            return True

        async def answer(self, tool_use_id: str, answers: tuple[NativeAnswer, ...]) -> bool:
            self.answered.append((tool_use_id, answers))
            return True

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    plan = json.dumps(
        {
            "session_id": "native-session",
            "tool_use_id": "toolu_q",
            "answers": [{"indexes": [1], "text": None}, {"indexes": [0, 2], "text": "extra"}],
        }
    )
    stream = (
        'event: registered\ndata: {"generation":"test"}\n\n'
        "event: delivery\ndata: "
        + json.dumps({"op": "answer", "message_id": "", "text": plan})
        + "\n\n"
    )
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        _once_then_revoked(lambda request: httpx.Response(200, text=stream))
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="do the thing",
    )
    await NativeWorker(config).run()
    assert native.answered == [
        ("toolu_q", (NativeAnswer(indexes=(1,)), NativeAnswer(indexes=(0, 2), text="extra")))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, ValueError("reader failed")])
@pytest.mark.parametrize("backoff", [False, True])
async def test_reader_termination_ends_worker_even_while_daemon_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, failure: Exception | None, backoff: bool
) -> None:
    connected = asyncio.Event()
    stream_closed = asyncio.Event()

    class Native:
        closed = False

        async def start(self, prompt: str) -> str:
            return "native-session"

        async def wait_closed(self) -> None:
            await connected.wait()
            if failure is not None:
                raise failure

        async def close(self) -> None:
            self.closed = True

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            connected.set()
            yield b'event: registered\ndata: {"generation":"test"}\n\n'
            await asyncio.Future()

        async def aclose(self) -> None:
            stream_closed.set()

    def handler(request: httpx.Request) -> httpx.Response:
        if backoff:
            connected.set()
            raise httpx.ConnectError("daemon unavailable")
        return httpx.Response(200, stream=Stream())

    native = Native()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 30)
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(
            base_url="http://localhost", transport=httpx.MockTransport(handler)
        ),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32, provider="claude_code", command=["unused"], resume=True
    )
    expected = ValueError if failure is not None else RuntimeError
    with pytest.raises(expected, match=r"reader failed|provider output closed"):
        await asyncio.wait_for(NativeWorker(config).run(), timeout=1)
    assert native.closed
    if not backoff:
        assert stream_closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["initial", "steer", "message"])
async def test_interrupt_bypasses_slow_input_and_preserves_queued_text(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    sending = asyncio.Event()
    interrupted = asyncio.Event()
    complete = asyncio.Event()
    sent: list[str] = []

    class Native:
        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            sent.append(text)
            if len(sent) == 1:
                sending.set()
                await interrupted.wait()
            if text == "second":
                complete.set()
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def interrupt(self) -> bool:
            interrupted.set()
            return True

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: registered\ndata: {"generation":"test"}\n\n'
            if kind != "initial":
                payload = {"op": kind, "message_id": "mbx_" + "1" * 32, "text": "first"}
                yield f"event: delivery\ndata: {json.dumps(payload)}\n\n".encode()
            await sending.wait()
            yield b'event: delivery\ndata: {"op":"steer","text":"second"}\n\n'
            yield b'event: delivery\ndata: {"op":"interrupt","text":""}\n\n'
            await complete.wait()

        async def aclose(self) -> None:
            pass

    native = Native()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    transport = httpx.MockTransport(
        _once_then_revoked(
            lambda request: (
                httpx.Response(200, json={})
                if request.url.path == "/mailboxes/ack"
                else httpx.Response(200, stream=Stream())
            )
        )
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="first" if kind == "initial" else "",
        # A worker with no task to submit is a resumed one in production.
        resume=kind != "initial",
    )
    await asyncio.wait_for(NativeWorker(config).run(), timeout=1)
    assert interrupted.is_set()
    assert sent[0].endswith("first")
    assert sent[1:] == ["second"]


@pytest.mark.asyncio
async def test_reconnect_reserves_unacknowledged_input_without_resending_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message_id = "mbx_" + "a" * 32
    sent: list[str] = []
    ack_failed = asyncio.Event()
    ack_succeeded = asyncio.Event()
    connections: list[list[str]] = []

    class Native:
        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            sent.append(text)
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'event: registered\ndata: {"generation":"test"}\n\n'
            if len(connections) == 1:
                payload = {"op": "steer", "message_id": message_id, "text": "once"}
                yield f"event: delivery\ndata: {json.dumps(payload)}\n\n".encode()
                await ack_failed.wait()
                raise httpx.ReadError("daemon restarted")
            await ack_succeeded.wait()

        async def aclose(self) -> None:
            pass

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/ack":
            if len(connections) == 1:
                ack_failed.set()
                return httpx.Response(503)
            ack_succeeded.set()
            return httpx.Response(200, json={})
        connections.append(request.url.params.get_list("pending_input_ids"))
        if len(connections) > 2:
            return httpx.Response(401)
        return httpx.Response(200, stream=Stream())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(NativeWorker, "transport", lambda self: Native())
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(
            base_url="http://localhost", transport=httpx.MockTransport(handler)
        ),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32, provider="claude_code", command=["unused"], resume=True
    )
    await asyncio.wait_for(NativeWorker(config).run(), timeout=1)
    assert connections == [[], [message_id], []]
    assert sent == ["once"]


@pytest.mark.asyncio
async def test_entrypoint_signals_cancel_worker_and_remove_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers = {}
    removed = []
    closed = asyncio.Event()
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "add_signal_handler", lambda signum, callback: handlers.update({signum: callback})
    )
    monkeypatch.setattr(loop, "remove_signal_handler", removed.append)

    async def run(self) -> None:
        try:
            await asyncio.Future()
        finally:
            closed.set()

    monkeypatch.setattr(NativeWorker, "run", run)
    config = NativeWorkerConfig(
        workspace_id="a" * 32, provider="claude_code", command=["unused"], resume=True
    )
    entry = asyncio.create_task(_run(config))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    handlers[signal.SIGHUP]()
    await asyncio.wait_for(entry, timeout=1)
    assert closed.is_set()
    assert set(removed) == {signal.SIGTERM, signal.SIGINT, signal.SIGHUP}


# ─── the pane is the wire log ───────────────────────────────────────────────


def test_a_frame_renders_as_one_stamped_bounded_pane_line() -> None:
    """One line per frame, an arrow for direction, and a cut that SAYS it cut.

    The pane is a tmux scrollback every surface captures, not a transcript
    store: an unbounded 40 KB replayed prompt would push the frames a person
    came to see off the top. The tail names how much was dropped so a cut
    frame never reads as a short one.
    """
    line = NativeWorker.frame_line("recv", {"type": "system", "subtype": "init"})
    stamp, arrow, body = line.split(" ", 2)
    assert len(stamp) == 12 and stamp[2] == ":" and stamp[8] == "."
    assert arrow == "←"
    assert body == '{"type":"system","subtype":"init"}'
    assert NativeWorker.frame_line("send", {"type": "user"}).split(" ", 2)[1] == "→"

    long = NativeWorker.frame_line("recv", {"text": "x" * 1000})
    assert long.endswith("… (+611 chars)")
    assert len(long.split(" ", 2)[2]) < 1000


def test_trace_prints_the_frame_line(capsys: pytest.CaptureFixture[str]) -> None:
    worker = NativeWorker(
        NativeWorkerConfig(workspace_id="a" * 32, provider="claude_code", command=["unused"])
    )
    worker.trace("send", {"type": "control_request", "request": {"subtype": "interrupt"}})
    out = capsys.readouterr().out
    assert out.count("\n") == 1
    assert '→ {"type":"control_request","request":{"subtype":"interrupt"}}' in out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resume", "provider"),
    [(True, "claude_code"), (False, "claude_code")],
)
async def test_only_a_resume_skips_the_boot_prompt_and_the_preamble(
    monkeypatch: pytest.MonkeyPatch, resume: bool, provider: str
) -> None:
    """`resume` is the respawn signal; an empty prompt is not.

    A resumed conversation already holds the work, so the worker must neither
    ask it to "reply READY" (interrupting whatever it is mid-task) nor deliver
    a fresh preamble. A FRESH session with no task is the opposite case wearing
    the same empty prompt: without a first user turn the provider writes no
    transcript, so the session reads as nonexistent everywhere.
    """

    class Native:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.sent: list[str] = []

        async def start(self, prompt: str) -> str:
            self.started.append(prompt)
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.sent.append(text)
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    real_client = httpx.AsyncClient
    stream = 'event: registered\ndata: {"generation":"test"}\n\n'
    transport = httpx.MockTransport(
        _once_then_revoked(lambda request: httpx.Response(200, text=stream))
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider=provider,
        command=["unused"],
        resume=resume,
        initial_prompt="",
    )
    await NativeWorker(config).run()

    if resume:
        assert native.started == [""]  # no boot prompt on a resume
        assert native.sent == []  # and no preamble either
    else:
        assert native.started == [_BOOT_PROMPT]
        assert native.sent == [_PEER_PREAMBLE]  # the preamble alone, no dangling separator


@pytest.mark.asyncio
async def test_worker_routes_compact_and_command_to_dedicated_native_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Controls must reach native provider verbs, never become a text prompt."""

    class Native:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.calls.append(("send", text))
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def interrupt(self) -> bool:
            self.calls.append(("interrupt", ""))
            return True

        async def set_model(self, model: str) -> bool:
            self.calls.append(("set_model", model))
            return True

        async def compact(self) -> bool:
            self.calls.append(("compact", ""))
            return True

        async def invoke_control(self, name: str) -> bool:
            self.calls.append(("command", name))
            return True

        async def answer(self, tool_use_id: str, answers: tuple[NativeAnswer, ...]) -> bool:
            return True

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    stream = (
        'event: registered\ndata: {"generation":"test"}\n\n'
        'event: delivery\ndata: {"op":"compact","message_id":"","text":""}\n\n'
        'event: delivery\ndata: {"op":"command","message_id":"","text":"review --staged"}\n\n'
    )
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        _once_then_revoked(lambda request: httpx.Response(200, text=stream))
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)

    await NativeWorker(
        NativeWorkerConfig(
            workspace_id="a" * 32,
            provider="claude_code",
            command=["unused"],
            resume=True,
        )
    ).run()

    assert native.calls == [("compact", ""), ("command", "review --staged")]


class _MacroNative:
    """An owner whose model moves with `/model` and whose `/bad` does not complete."""

    def __init__(self) -> None:
        self.current_model: str | None = "opus-1m"
        self.ran: list[str] = []

    async def run_command(self, command: str) -> bool:
        self.ran.append(command)
        if command.startswith("/model "):
            self.current_model = command.split(" ", 1)[1]
        return command != "/bad"


def _worker() -> NativeWorker:
    return NativeWorker(
        NativeWorkerConfig(workspace_id="a" * 32, provider="claude_code", command=["unused"])
    )


@pytest.mark.asyncio
async def test_a_grove_command_restores_the_model_it_started_on() -> None:
    """`{model}` is captured ONCE, before the first step: the command switches
    away, and a per-step expansion would hand back the model it switched TO."""
    native = _MacroNative()
    await _worker()._run_macro(
        native,  # type: ignore[arg-type]
        {"name": "compact-fast", "steps": ["/model flash", "/compact", "/model {model}"]},
    )
    assert native.ran == ["/model flash", "/compact", "/model opus-1m"]


@pytest.mark.asyncio
async def test_a_grove_command_stops_at_the_first_step_that_did_not_complete() -> None:
    native = _MacroNative()
    await _worker()._run_macro(
        native,  # type: ignore[arg-type]
        {"name": "x", "steps": ["/model flash", "/bad", "/model {model}"]},
    )
    assert native.ran == ["/model flash", "/bad"]


@pytest.mark.asyncio
async def test_a_grove_command_with_no_reported_model_restores_the_default() -> None:
    native = _MacroNative()
    native.current_model = None
    await _worker()._run_macro(
        native,  # type: ignore[arg-type]
        {"name": "x", "steps": ["/model {model}"]},
    )
    assert native.ran == ["/model default"]


@pytest.mark.asyncio
async def test_a_second_grove_command_while_one_runs_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = asyncio.Event()

    class Slow(_MacroNative):
        async def run_command(self, command: str) -> bool:
            self.ran.append(command)
            await gate.wait()
            return True

    native = Slow()
    worker = _worker()
    worker._start_macro(native, {"name": "a", "steps": ["/compact"]})  # type: ignore[arg-type]
    await asyncio.sleep(0)
    worker._start_macro(native, {"name": "b", "steps": ["/other"]})  # type: ignore[arg-type]
    gate.set()
    assert worker._macro is not None
    await worker._macro
    assert native.ran == ["/compact"]
    assert "/grove:b refused" in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize("watched", [True, False])
async def test_the_live_pulse_is_posted_only_while_the_daemon_says_someone_watches(
    monkeypatch: pytest.MonkeyPatch, watched: bool
) -> None:
    """The fold runs on every frame; the POST happens only after a `watch on`.

    An unwatched session must cost the daemon nothing, so the negative case
    asserts that not a single `/mailboxes/pulse` request was made.
    """

    class Native:
        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            worker._observe(
                "recv", {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 300}
            )
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    pulses: list[dict[str, object]] = []
    posted = asyncio.Event()

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/pulse":
            pulses.append(json.loads(request.content))
            posted.set()
            return httpx.Response(200, json={"status": "ok"})
        assert request.url.params.get("live") == "1"
        watch = 'event: delivery\ndata: {"op":"watch","message_id":"","text":"on"}\n\n'
        head = 'event: registered\ndata: {"generation":"test"}\n\n' + (watch if watched else "")

        async def body():  # a daemon connection stays open; its end clears the watch
            yield head.encode()
            await asyncio.Future()

        return httpx.Response(200, content=body())

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(route)
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32, provider="claude_code", command=["unused"], initial_prompt="go"
    )
    worker = NativeWorker(config)
    run = asyncio.create_task(worker.run())
    try:
        if watched:
            await asyncio.wait_for(posted.wait(), 5)
            assert pulses[-1]["step"] == "thinking"
            assert pulses[-1]["thinking_tokens"] == 300
        else:
            await asyncio.sleep(0.3)
            assert pulses == []
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_watch_turned_off_stops_the_posts_even_with_frames_still_flowing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each gate alone must hold: a viewer leaving mid-thought ends the posts.

    Drives the publisher directly with a watch that was ON (so a change is
    already pending) and is then turned OFF while frames keep arriving.
    """
    posts: list[object] = []

    class Client:
        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, path: str, **kwargs: object) -> httpx.Response:
            posts.append(kwargs.get("json"))
            return httpx.Response(200, request=httpx.Request("POST", path))

    monkeypatch.setattr("grove.core.native_worker._PULSE_INTERVAL_SECONDS", 0.01)
    worker = NativeWorker(
        NativeWorkerConfig(workspace_id="a" * 32, provider="claude_code", command=["unused"])
    )
    monkeypatch.setattr(worker, "_client", lambda **_: Client())
    publisher = asyncio.create_task(worker._publish_pulse())
    try:
        worker._set_watched(True)
        await asyncio.sleep(0.05)
        seen = len(posts)
        assert seen >= 1

        worker._set_watched(False)
        for tokens in range(10, 200, 10):
            worker._observe(
                "recv", {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": tokens}
            )
            await asyncio.sleep(0.01)
        assert len(posts) == seen
    finally:
        publisher.cancel()
        await asyncio.gather(publisher, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_create_that_resumes_with_a_task_delivers_the_task_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create may resume a session AND carry a task. The task is the first
    input to the continued conversation: no boot prompt, no preamble (the
    session already has one), and never silently dropped."""

    class Native:
        def __init__(self) -> None:
            self.started: list[str] = []
            self.sent: list[str] = []

        async def start(self, prompt: str) -> str:
            self.started.append(prompt)
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            self.sent.append(text)
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    real_client = httpx.AsyncClient
    stream = 'event: registered\ndata: {"generation":"test"}\n\n'
    transport = httpx.MockTransport(
        _once_then_revoked(lambda request: httpx.Response(200, text=stream))
    )
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        resume=True,
        initial_prompt="now add the tests",
    )
    await NativeWorker(config).run()

    assert native.started == [""]
    assert native.sent == ["now add the tests"]


@pytest.mark.parametrize(
    ("found", "flag", "resume"), [(True, "--resume", True), (False, "--session-id", False)]
)
def test_a_woken_remote_worker_resumes_only_a_transcript_it_holds(
    monkeypatch: pytest.MonkeyPatch, found: bool, flag: str, resume: bool
) -> None:
    from grove.core import native_worker  # noqa: PLC0415

    class Adapter:
        def locate_transcripts(self, cwd: object, session_id: str) -> list[str]:
            return ["t.jsonl"] if found else []

    monkeypatch.setattr(native_worker, "get_adapter", lambda kind: Adapter())
    sid = "6f1d1b8e-0000-4000-8000-000000000001"
    config = native_worker.NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["claude", "--resume", sid],
        resume=True,
        repo_url="https://git.example.test/acme/app.git",
        branch="grove/a",
    )
    result = native_worker._resumable(config)
    assert result.command == ["claude", flag, sid] and result.resume is resume


def _remote_spool_harness(
    monkeypatch: pytest.MonkeyPatch, first_status: int
) -> tuple[NativeWorker, list[tuple[str, dict[str, object]]], asyncio.Event]:
    """A remote worker whose daemon answers the FIRST spool post with ``first_status``."""

    class Native:
        async def start(self, prompt: str) -> str:
            return "native-session"

        async def send(self, message_id: str, text: str) -> NativeSubmission:
            return NativeSubmission(stage="delivered", evidence=message_id)

        async def wait_closed(self) -> None:
            await asyncio.Future()

        async def close(self) -> None:
            pass

    landed: list[tuple[str, dict[str, object]]] = []
    refusals = [first_status]
    stop_landed = asyncio.Event()

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/spool":
            if refusals:
                return httpx.Response(refusals.pop())
            assert request.url.params["generation"] == "g" * 32
            body = json.loads(request.content)
            landed.append((request.url.params["suffix"], body))
            if body.get("hook_event_name") == "Stop":
                stop_landed.set()
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/mailboxes/connection":
            head = 'event: registered\ndata: {"generation":"' + "g" * 32 + '"}\n\n'

            async def body():
                yield head.encode()
                await asyncio.Future()

            return httpx.Response(200, content=body())
        return httpx.Response(200, json={"status": "ok"})

    native = Native()
    monkeypatch.setattr(NativeWorker, "transport", lambda self: native)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(route)
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(base_url="http://localhost", transport=transport),
    )
    worker = NativeWorker(
        NativeWorkerConfig(
            workspace_id="a" * 32,
            provider="claude_code",
            command=["unused"],
            resume=True,
            repo_url="http://forge.example/acme/widgets.git",
            branch="grove/x",
        )
    )
    return worker, landed, stop_landed


async def _two_turn_edges(worker: NativeWorker, landed_stop: asyncio.Event) -> None:
    run = asyncio.create_task(worker.run())
    try:
        await asyncio.sleep(0.05)
        assert worker._asks is not None
        worker._asks.bind("native-session")
        worker._asks.turn("UserPromptSubmit")
        worker._asks.turn("Stop")
        await asyncio.wait_for(landed_stop.wait(), 5)
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.asyncio
async def test_a_remote_worker_posts_its_spool_drops_in_order_and_retries_a_refused_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A remote worker shares no filesystem with the host, so its drops ride the
    daemon connection (#1030). The first post meets a restarting daemon (503):
    if the sender dropped it, the turn would never read as started."""
    worker, landed, stop_landed = _remote_spool_harness(monkeypatch, 503)
    await _two_turn_edges(worker, stop_landed)
    assert [body["hook_event_name"] for _, body in landed] == ["UserPromptSubmit", "Stop"]
    assert {suffix for suffix, _ in landed} == {".turn.json"}


@pytest.mark.asyncio
async def test_a_permanently_refused_drop_is_discarded_so_the_next_edge_still_lands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 413 will answer the same drop forever. Retrying it would hold every
    later edge behind it, so the turn end would never land and the session would
    read WORKING: the refused drop must go, and the `Stop` behind it must arrive."""
    worker, landed, stop_landed = _remote_spool_harness(monkeypatch, 413)
    await _two_turn_edges(worker, stop_landed)
    assert [body["hook_event_name"] for _, body in landed] == ["Stop"]
