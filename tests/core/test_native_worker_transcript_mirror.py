"""A remote worker mirrors its transcript files to the daemon by byte offset (#1099).

Driven end to end against a mock daemon that holds the host's copy: the
provider "writes" its transcript (a real file under a temp ``CLAUDE_CONFIG_DIR``,
located the way the real adapter locates it), the worker follows it, the daemon
connection is cut and re-served mid-session, and the host's bytes must equal
the worker's file exactly — no duplicate from the replay, no gap from the cut.
A sub-agent transcript appearing mid-session must be picked up too, because the
fleet rows read those.
"""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.native_owner import NativeSubmission
from grove.core.native_worker import NativeWorker, NativeWorkerConfig

_SESSION = "11111111-2222-3333-4444-555555555555"


class _HostCopy:
    """The daemon's side of the protocol, in memory: one buffer per path."""

    def __init__(self) -> None:
        self.files: dict[str, bytearray] = {}
        self.chunks: list[tuple[str, int, int]] = []
        self.connections = 0
        self.declared: dict[str, str] = {}
        self.revoked = False

    def route(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mailboxes/connection":
            self.connections += 1
            self.declared = dict(request.url.params)
            if self.revoked:
                return httpx.Response(401, json={"detail": "revoked"})
            # Every stream ends on its own right after registering (a daemon
            # restart each time), so the worker is reconnecting throughout and
            # every chunk after the first crosses a fresh generation.
            body = f'event: registered\ndata: {{"generation":"g{self.connections}"}}\n\n'
            return httpx.Response(200, text=body)
        if request.url.path == "/mailboxes/transcript":
            body = json.loads(request.content)
            path, offset = body["relative_path"], body["offset"]
            data = base64.b64decode(body["data"])
            held = self.files.setdefault(path, bytearray())
            self.chunks.append((path, offset, len(data)))
            if offset != len(held):
                return httpx.Response(409, json={"detail": {"size": len(held)}})
            held.extend(data)
            return httpx.Response(200, json={"size": len(held)})
        return httpx.Response(200, json={"status": "ok"})


class _Provider:
    """A provider that appends to its transcript when told to emit a frame."""

    def __init__(self, transcript: Path, observe: Any) -> None:
        self.transcript = transcript
        self._observe = observe
        self.closed = asyncio.Event()

    async def start(self, prompt: str) -> str:
        return _SESSION

    async def send(self, message_id: str, text: str) -> NativeSubmission:
        return NativeSubmission(stage="delivered")

    async def wait_closed(self) -> None:
        await self.closed.wait()

    async def close(self) -> None:
        self.closed.set()

    def emit_line(self, record: dict[str, Any]) -> None:
        with self.transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        self._observe("recv", record)


@dataclass
class _Scenario:
    """A running remote worker, its provider, and the host's copy."""

    host: _HostCopy
    provider: _Provider
    transcript: Path
    project: Path
    cwd: Path
    run: asyncio.Task[None]

    @property
    def relative(self) -> str:
        return f"projects/{_ClaudeHome.encode_cwd(self.cwd)}/{_SESSION}.jsonl"

    async def settled(self, expected: bytes, path: str | None = None) -> None:
        path = path or self.relative
        for _ in range(300):
            if bytes(self.host.files.get(path, b"")) == expected:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"host copy never caught up: {self.host.files.get(path)!r}")

    async def wait_reconnect(self) -> None:
        before = self.host.connections
        for _ in range(300):
            if self.host.connections > before:
                return
            await asyncio.sleep(0.01)


async def _start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Scenario:
    """Run a REMOTE claude_code worker whose transcript is a real file under a
    temp ``CLAUDE_CONFIG_DIR``, against the in-memory host."""
    config_dir = tmp_path / "claude-home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    # The adapter's own encoding of this cwd is what the locate's fast path reads.
    project = config_dir / "projects" / _ClaudeHome.encode_cwd(cwd)
    project.mkdir(parents=True)
    transcript = project / f"{_SESSION}.jsonl"
    transcript.touch()

    host = _HostCopy()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(
            base_url="http://localhost", transport=httpx.MockTransport(host.route)
        ),
    )
    providers: list[_Provider] = []

    def transport(self: NativeWorker) -> _Provider:
        providers.append(_Provider(transcript, self._observe))
        return providers[-1]

    monkeypatch.setattr(NativeWorker, "transport", transport)
    monkeypatch.setattr("grove.core.native_worker._MIRROR_SETTLE_SECONDS", 0.01)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_FLOOR_SECONDS", 0.01)
    monkeypatch.setattr("grove.core.native_worker._RECONNECT_CEILING_SECONDS", 0.02)
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="claude_code",
        command=["unused"],
        initial_prompt="",
        repo_url="https://example.invalid/acme/widgets.git",
        branch="grove/x",
    )
    run = asyncio.create_task(NativeWorker(config).run())
    for _ in range(50):
        if providers:
            break
        await asyncio.sleep(0.01)
    return _Scenario(host, providers[0], transcript, project, cwd, run)


@pytest.mark.asyncio
async def test_the_host_copy_equals_the_workers_file_across_a_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sc = await _start(tmp_path, monkeypatch)
    sc.provider.emit_line({"type": "user", "n": 1})
    await sc.settled(sc.transcript.read_bytes())
    # Two lines land while the first stream is being torn down.
    sc.provider.emit_line({"type": "assistant", "n": 2})
    sc.provider.emit_line({"type": "user", "n": 3})
    await sc.settled(sc.transcript.read_bytes())

    # A sub-agent transcript appears mid-session and is picked up on the next edge.
    sub = sc.project / _SESSION / "subagents" / "agent-abc.jsonl"
    sub.parent.mkdir(parents=True)
    sub.write_text('{"type":"assistant","sub":true}\n', encoding="utf-8")
    sc.provider.emit_line({"type": "assistant", "n": 4})
    await sc.settled(sc.transcript.read_bytes())
    await sc.settled(
        sub.read_bytes(),
        f"projects/{_ClaudeHome.encode_cwd(sc.cwd)}/{_SESSION}/subagents/agent-abc.jsonl",
    )

    assert sc.host.connections >= 2, "the reconnect never happened"
    assert sc.host.declared.get("transcripts") == "1"
    assert sc.host.declared.get("cwd") == str(sc.cwd)
    # No byte was applied twice: every accepted chunk started exactly at the
    # size the host held, and the final copies are byte-equal.
    assert bytes(sc.host.files[sc.relative]) == sc.transcript.read_bytes()
    sc.host.revoked = True
    await asyncio.wait_for(sc.run, timeout=5)


@pytest.mark.asyncio
async def test_a_host_that_lost_bytes_is_caught_up_off_its_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host LOSES part of its copy between two connections (a daemon
    restored from an older state dir). The worker's own offsets still say
    everything was sent, so only the host's 409 (carrying its real size)
    can correct it; delete the 409 arm and the host stays short forever."""
    sc = await _start(tmp_path, monkeypatch)
    sc.provider.emit_line({"type": "user", "n": 1})
    sc.provider.emit_line({"type": "assistant", "n": 2})
    await sc.settled(sc.transcript.read_bytes())

    sc.host.files[sc.relative] = bytearray(sc.host.files[sc.relative][:10])
    await sc.wait_reconnect()
    sc.provider.emit_line({"type": "assistant", "n": 3})
    await sc.settled(sc.transcript.read_bytes())
    assert any(offset == 10 for _, offset, _ in sc.host.chunks), "the 409 size was not adopted"
    sc.host.revoked = True
    await asyncio.wait_for(sc.run, timeout=5)


@pytest.mark.asyncio
async def test_a_host_or_container_worker_mirrors_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a REMOTE worker (one that clones) replicates; a worker sharing the
    host's filesystem would be copying a file onto itself."""
    config = NativeWorkerConfig(
        workspace_id="a" * 32, provider="claude_code", command=["unused"], initial_prompt=""
    )
    worker = NativeWorker(config)
    assert worker._mirrors() is False
    await worker._mirror_transcripts()  # returns at once rather than looping


def test_a_tool_with_no_replicable_root_contributes_no_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenCode's SQLite store is located but never mirrored: the adapter
    declares no root, so the worker has nothing to send for it."""
    monkeypatch.chdir(tmp_path)
    config = NativeWorkerConfig(
        workspace_id="a" * 32,
        provider="opencode",
        command=["unused"],
        initial_prompt="",
        repo_url="https://example.invalid/acme/widgets.git",
    )
    worker = NativeWorker(config)
    worker._session_id = "ses_x"
    monkeypatch.setattr(
        "grove.core.agents.opencode.OpencodeAdapter.locate_transcripts",
        lambda self, cwd, sid: [tmp_path / "opencode.db"],
    )
    assert worker._mirror_files() == []


@pytest.mark.asyncio
async def test_a_remote_worker_posts_its_pane_only_while_watched_and_resends_after_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1101: unwatched, nothing is posted; a watch sends the whole log; a 409
    from the daemon makes the next post a reset rather than leaving a gap."""
    posts: list[dict[str, Any]] = []
    refuse = {"next": False}

    def route(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        posts.append(body)
        if refuse["next"]:
            refuse["next"] = False
            return httpx.Response(409, json={"detail": {"end": 0}})
        return httpx.Response(200, json={"end": body["first"] + len(body["lines"])})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "grove.core.native_worker.httpx.AsyncClient",
        lambda **kwargs: real_client(
            base_url="http://localhost", transport=httpx.MockTransport(route)
        ),
    )
    monkeypatch.setattr("grove.core.native_worker._PULSE_INTERVAL_SECONDS", 0.01)
    worker = NativeWorker(
        NativeWorkerConfig(
            workspace_id="a" * 32,
            provider="claude_code",
            command=["unused"],
            initial_prompt="",
            repo_url="https://example.invalid/acme/widgets.git",
        )
    )
    task = asyncio.create_task(worker._publish_pane())
    worker.emit("one")
    await asyncio.sleep(0.05)
    assert posts == []

    worker._set_watched(True)
    await asyncio.sleep(0.05)
    assert posts[-1] == {"first": 0, "lines": ["one"], "reset": True}

    refuse["next"] = True
    worker.emit("two")
    await asyncio.sleep(0.05)
    worker.emit("three")
    await asyncio.sleep(0.05)
    # The refused batch is followed by the whole log, then plain increments.
    assert {"first": 0, "lines": ["one", "two"], "reset": True} in posts
    assert posts[-1] == {"first": 2, "lines": ["three"], "reset": False}
    task.cancel()
