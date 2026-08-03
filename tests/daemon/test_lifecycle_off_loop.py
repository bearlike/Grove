"""Lifecycle verbs must not run on the daemon's event loop, nor interleave per workspace.

Every route here used to call the manager synchronously inside its ``async
def``, so a container ``create`` — a full ``devcontainer up``, minutes long —
froze the whole process: every SSE stream, every other repo's dashboard, the
activity poll, every read route on the host. These tests pin the property that
matters (the loop keeps serving while a verb is in flight), not the plumbing:
each drives one lifecycle request that blocks inside the manager on an event
the test owns, and asserts a second request is served meanwhile.

Moving verbs off the loop deletes an invariant: the single-threaded loop used to
be an implicit mutex over the whole lifecycle surface, and once every verb runs
on a pool, two verbs on the SAME workspace genuinely interleave — a kill
removing a worktree while a respawn is halfway through ``devcontainer up``.
Two workspaces must still overlap, since that is the entire point of the
pool, so both directions are pinned here.

Deterministic throughout — no wall-clock sleeps, every hand-off is an explicit
event or await, following ``test_hook_ingest.py``'s coalescer test.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config

# Generous bounds: they exist to fail a wedged test, never to time anything.
_ENTERED_TIMEOUT_S = 5.0
_OTHER_REQUEST_TIMEOUT_S = 5.0
_BLOCK_TIMEOUT_S = 5.0
# The one bound that IS a measurement: how long we let a second verb on the same
# workspace try to get in before concluding it is held out. It cannot produce a
# false failure (a held lock never releases during the test), only a false pass
# if the pool were slower than this — hence generous rather than snappy.
_NO_SECOND_ENTRY_TIMEOUT_S = 2.0


class _BlockedVerb:
    """A manager verb held mid-call until the test releases it.

    Owns both halves of the hand-off (the "we are inside the verb" signal and
    the release) plus the thread it ran on, so a test reads as three lines.
    """

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.released = threading.Event()
        self.thread_name: str | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch, verb: str) -> None:
        """Wrap ``WorkspaceManager.<verb>`` so it blocks until released.

        Patches the manager's PUBLIC method — the same discipline as the fake
        tmux/git seams: rename it and this breaks loudly.
        """
        original = getattr(WorkspaceManager, verb)

        def _blocking(self_: WorkspaceManager, *args: Any, **kwargs: Any) -> Any:
            self.thread_name = threading.current_thread().name
            self.entered.set()
            if not self.released.wait(timeout=_BLOCK_TIMEOUT_S):
                raise AssertionError(
                    f"{verb} was never released — the event loop could not run the "
                    "request that was supposed to release it, i.e. the verb ran ON "
                    "the loop"
                )
            return original(self_, *args, **kwargs)

        monkeypatch.setattr(WorkspaceManager, verb, _blocking)


class _ConcurrentVerb:
    """A manager verb that records every entry and blocks until the test releases it.

    The sibling of ``_BlockedVerb`` for the serialization question: it does not
    ask "did we get inside", it asks "how many calls are inside AT ONCE, and for
    which workspaces". Entries are recorded by workspace id, which is exactly the
    key the runner serializes on.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self.entries: list[str] = []
        self.released = threading.Event()

    def install(self, monkeypatch: pytest.MonkeyPatch, verb: str) -> None:
        original = getattr(WorkspaceManager, verb)

        def _blocking(self_: WorkspaceManager, ws_id: str, *args: Any, **kwargs: Any) -> Any:
            with self._cond:
                self.entries.append(ws_id)
                self._cond.notify_all()
            if not self.released.wait(timeout=_BLOCK_TIMEOUT_S):
                raise AssertionError(f"{verb} was never released")
            return original(self_, ws_id, *args, **kwargs)

        monkeypatch.setattr(WorkspaceManager, verb, _blocking)

    def wait_for_entries(self, count: int, timeout: float) -> bool:
        """Block (off the loop — bridge through an executor) until ``count`` are inside."""
        with self._cond:
            return self._cond.wait_for(lambda: len(self.entries) >= count, timeout=timeout)


@pytest.fixture
def daemon_app(
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Iterator[httpx.AsyncClient]:
    """An async client over the app, driven WITHOUT lifespan.

    ``httpx.ASGITransport`` runs no startup/shutdown events, which is what these
    tests want: no background activity poll competing for the executor, so a
    served request is attributable to this test alone.
    """
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    transport = httpx.ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://test")


async def _create_workspace(
    client: httpx.AsyncClient, repo_root: Path, title: str = "off-loop test"
) -> str:
    """Create a workspace. ``title`` seeds the auto branch name, so two
    workspaces in one test need two titles — the generated name is only
    second-resolution, and a collision 409s."""
    resp = await client.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": title,
            "repo_root": str(repo_root),
            "branch_plan": {"kind": "auto"},
        },
    )
    assert resp.status_code == 200, resp.text
    workspace_id: str = resp.json()["id"]
    return workspace_id


async def _assert_loop_stays_live(
    client: httpx.AsyncClient,
    blocked: _BlockedVerb,
    send_lifecycle: Callable[[], Any],
) -> httpx.Response:
    """Drive ``send_lifecycle`` to its blocking point, then prove the loop runs.

    ``/healthz`` is the probe on purpose: it touches no manager, so it can only
    fail to be served if the loop itself is stuck.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.create_task(send_lifecycle())
    # Bridged through the executor so awaiting it yields the loop, letting the
    # request actually reach the manager call — a bare `wait()` here would
    # block the loop thread itself and deadlock.
    assert await loop.run_in_executor(None, blocked.entered.wait, _ENTERED_TIMEOUT_S), (
        "the lifecycle verb was never entered"
    )
    health = await asyncio.wait_for(client.get("/healthz"), timeout=_OTHER_REQUEST_TIMEOUT_S)
    assert health.status_code == 200
    # Still blocked: the loop served another request DURING the verb, not after it.
    assert not task.done()
    blocked.released.set()
    response: httpx.Response = await task
    return response


async def test_create_serves_other_requests_while_provisioning(
    daemon_app: httpx.AsyncClient,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A create in flight must not stall the rest of the daemon.

    With containers the default runtime, a create is minutes of blocking
    work, and on the loop that takes every stream and every other repo's
    dashboard down with it.
    """
    blocked = _BlockedVerb()
    blocked.install(monkeypatch, "create")
    async with daemon_app as client:
        resp = await _assert_loop_stays_live(
            client,
            blocked,
            lambda: client.post(
                "/workspaces",
                json={
                    "agent_name": "claude",
                    "title": "slow create",
                    "repo_root": str(tmp_repo),
                    "branch_plan": {"kind": "auto"},
                },
            ),
        )
    assert resp.status_code == 200, resp.text


async def test_create_runs_off_the_shared_read_pool(
    daemon_app: httpx.AsyncClient,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle work runs on its OWN pool, not the default executor.

    The default pool (``run_in_executor(None, ...)``) is the render path —
    every read route, the pane captures, the activity poll behind the SSE
    stream. Multi-minute provisions sharing it would trade a total stall for
    starvation of exactly those, so the isolation is the fix, not an
    implementation detail; the thread-name prefix is how an operator sees it
    too (``ps``, ``py-spy``).
    """
    blocked = _BlockedVerb()
    blocked.install(monkeypatch, "create")
    async with daemon_app as client:
        await _assert_loop_stays_live(
            client,
            blocked,
            lambda: client.post(
                "/workspaces",
                json={
                    "agent_name": "claude",
                    "title": "pool check",
                    "repo_root": str(tmp_repo),
                    "branch_plan": {"kind": "auto"},
                },
            ),
        )
    assert blocked.thread_name is not None
    assert blocked.thread_name.startswith("grove-lifecycle"), blocked.thread_name


@pytest.mark.parametrize(
    ("verb", "path_suffix", "body"),
    [
        ("pause", "/pause", {"force": False}),
        ("resume", "/resume", None),
        ("respawn", "/respawn", None),
        ("kill", "/kill", {"delete_branch": False}),
    ],
)
async def test_mutating_verb_serves_other_requests_while_blocked(
    daemon_app: httpx.AsyncClient,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
    path_suffix: str,
    body: dict[str, Any] | None,
) -> None:
    """Every lifecycle verb, not just create, keeps off the loop.

    All four re-enter the container runtime for a containerized workspace —
    pause runs a bounded in-container shutdown, resume/respawn re-provision,
    kill removes the container — so each is slow blocking work.

    The verb's own status is not this module's contract (a running workspace
    legitimately refuses ``resume``/``respawn`` with a 409); only "no server
    error, and the loop kept serving" is. Statuses live in
    ``test_workspaces_lifecycle.py``.
    """
    async with daemon_app as client:
        ws_id = await _create_workspace(client, tmp_repo)
        # Patched only AFTER the fixture workspace exists, so the setup create
        # runs the ordinary path.
        blocked = _BlockedVerb()
        blocked.install(monkeypatch, verb)
        resp = await _assert_loop_stays_live(
            client,
            blocked,
            lambda: client.post(f"/workspaces/{ws_id}{path_suffix}", json=body),
        )
    assert resp.status_code < 500, resp.text


# ─── per-workspace serialization ───────────────────────────────────────────


async def test_two_verbs_on_one_workspace_serialize(
    daemon_app: httpx.AsyncClient,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second verb on the SAME workspace waits for the first to finish.

    This is the invariant the single-threaded loop used to supply for free. The
    damage it prevents is not a corrupted store file — ``save()`` holds an
    exclusive flock across its whole read-modify-write — but the span BETWEEN a
    verb's read and its save, where it mutates git worktrees, tmux sessions and
    container runtimes. A kill tearing down the worktree while a respawn is
    midway through ``devcontainer up`` leaves a half-destroyed container and a
    record describing neither state.
    """
    async with daemon_app as client:
        ws_id = await _create_workspace(client, tmp_repo)
        verb = _ConcurrentVerb()
        verb.install(monkeypatch, "respawn")
        loop = asyncio.get_running_loop()
        first = asyncio.create_task(client.post(f"/workspaces/{ws_id}/respawn"))
        assert await loop.run_in_executor(None, verb.wait_for_entries, 1, _ENTERED_TIMEOUT_S)

        second = asyncio.create_task(client.post(f"/workspaces/{ws_id}/respawn"))
        # Given every chance to get in, and it must not: the first still holds
        # the workspace's lock. Without serialization this returns True — the
        # pool has seven idle workers waiting to run it.
        entered_twice = await loop.run_in_executor(
            None, verb.wait_for_entries, 2, _NO_SECOND_ENTRY_TIMEOUT_S
        )
        assert not entered_twice, f"both verbs ran at once on {ws_id}: {verb.entries}"

        verb.released.set()
        for resp in await asyncio.gather(first, second):
            assert resp.status_code < 500, resp.text
    # Both eventually ran — serialized, not dropped.
    assert verb.entries == [ws_id, ws_id]


async def test_verbs_on_different_workspaces_overlap(
    daemon_app: httpx.AsyncClient,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two workspaces still run their verbs concurrently — the point of the pool.

    Serializing on anything coarser than the workspace id (one global lock, the
    manager, the repo) would pass the test above and quietly restore the same
    freeze moving verbs off the loop is meant to remove: one multi-minute
    container provision blocking every other workspace's lifecycle request
    behind it.
    """
    async with daemon_app as client:
        first_id = await _create_workspace(client, tmp_repo, title="overlap one")
        second_id = await _create_workspace(client, tmp_repo, title="overlap two")
        verb = _ConcurrentVerb()
        verb.install(monkeypatch, "respawn")
        loop = asyncio.get_running_loop()
        requests = [
            asyncio.create_task(client.post(f"/workspaces/{ws}/respawn"))
            for ws in (first_id, second_id)
        ]
        # Positive assertion, so no timeout ambiguity: both are inside at once,
        # or this fails.
        assert await loop.run_in_executor(None, verb.wait_for_entries, 2, _ENTERED_TIMEOUT_S), (
            f"only {verb.entries} got in — verbs on distinct workspaces are serialized"
        )
        assert set(verb.entries) == {first_id, second_id}
        verb.released.set()
        for resp in await asyncio.gather(*requests):
            assert resp.status_code < 500, resp.text
