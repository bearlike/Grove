"""A daemon-to-worker worktree read: one request, one correlated answer, or a typed refusal."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.native_owners import (
    NativeOwnerRegistry,
    OwnerBinding,
    OwnerIdentity,
    OwnerUnavailable,
    WorktreeRead,
)
from grove.core.native_worker import NativeWorker, NativeWorkerConfig, _run_bounded

_WS = "a" * 32


def _register(registry: NativeOwnerRegistry, *, reads: bool) -> OwnerBinding:
    identity = OwnerIdentity(address=MailboxAddress(workspace_id=_WS), generation="g" * 32)
    return registry.register(identity, "session", reads=reads)


async def test_a_reconnected_owner_gives_the_next_steer_a_new_input_identifier() -> None:
    """A pre-restart receipt reservation cannot absorb a post-restart steer."""
    registry = NativeOwnerRegistry()
    pending = "own_00000000"
    binding = registry.register(
        OwnerIdentity(address=MailboxAddress(workspace_id=_WS), generation="g" * 32),
        "session",
        input_capacity=16,
        pending_input_ids=(pending,),
    )

    registry.control(_WS, "steer", "after restart")

    frame = await registry.next_frame(binding)
    assert frame.message_id != pending
    assert frame.text == "after restart"


async def test_a_read_is_answered_by_the_result_carrying_its_request_id() -> None:
    registry = NativeOwnerRegistry()
    binding = _register(registry, reads=True)

    async def worker() -> None:
        frame = await registry.next_frame(binding)
        assert frame.op == "read"
        assert json.loads(frame.text)["argv"] == ["git", "status"]
        registry.resolve_read(binding, frame.message_id, WorktreeRead(0, "clean"))

    serving = asyncio.create_task(worker())
    answered = await registry.read(_WS, ["git", "status"], timeout=5)
    await serving
    assert answered == WorktreeRead(0, "clean")
    assert not binding.pending_reads


async def test_an_unanswered_read_times_out_as_a_typed_refusal() -> None:
    registry = NativeOwnerRegistry()
    binding = _register(registry, reads=True)
    with pytest.raises(OwnerUnavailable, match="did not answer"):
        await registry.read(_WS, ["true"], timeout=0.05)
    assert not binding.pending_reads


async def test_an_owner_that_never_declared_reads_is_never_sent_one() -> None:
    registry = NativeOwnerRegistry()
    binding = _register(registry, reads=False)
    with pytest.raises(OwnerUnavailable):
        await registry.read(_WS, ["true"], timeout=1)
    assert binding.queue.empty()


async def test_an_owner_leaving_mid_read_fails_the_waiter_rather_than_hanging() -> None:
    registry = NativeOwnerRegistry()
    binding = _register(registry, reads=True)
    waiting = asyncio.create_task(registry.read(_WS, ["true"], timeout=5))
    await asyncio.sleep(0)
    registry.unregister(binding)
    with pytest.raises(OwnerUnavailable):
        await waiting


async def test_the_worker_runs_bounded_and_kills_an_overrun() -> None:
    assert await _run_bounded([sys.executable, "-c", "print('hi')"], timeout=10) == (0, "hi\n")
    code, why = await _run_bounded(
        [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.2
    )
    assert code == -1 and "killed" in why


async def test_a_tail_asks_the_worker_for_its_transcript_not_an_argv() -> None:
    registry = NativeOwnerRegistry()
    binding = _register(registry, reads=True)

    async def worker() -> None:
        frame = await registry.next_frame(binding)
        request = json.loads(frame.text)
        assert request["tail"] == 40 and "argv" not in request
        registry.resolve_read(binding, frame.message_id, WorktreeRead(0, "last line\n"))

    serving = asyncio.create_task(worker())
    assert await registry.tail(_WS, 40, timeout=5) == WorktreeRead(0, "last line\n")
    await serving


def test_the_worker_tails_its_own_transcript_and_none_before_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("".join(f"line {n}\n" for n in range(10)))

    class Adapter:
        def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
            assert session_id == "sess-1"
            return [transcript]

    monkeypatch.setattr("grove.core.native_worker.get_adapter", lambda kind: Adapter())
    worker = NativeWorker(
        NativeWorkerConfig(workspace_id=_WS, provider="claude_code", command=["claude"])
    )
    assert worker._transcript_tail(3) == (0, "")
    worker._session_id = "sess-1"
    assert worker._transcript_tail(3) == (0, "line 7\nline 8\nline 9\n")
