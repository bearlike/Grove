"""Lifecycle verbs must not occupy the UI thread.

The property under test is *liveness during a slow verb*, not "the verb
works" — a test that only asserts the workspace was created passes just as
happily against the frozen-UI implementation. Each test therefore drives a
manager double whose verb blocks on an event the test controls, proves the
app still processes input while it is in flight, and only then releases it.

The double reports whether it was released by the event or by its own
timeout: against the pre-fix code the UI thread is inside the verb, so the
test cannot reach the release, the wait times out, and `released_cleanly`
is False. That flag is what makes the pre-fix failure deterministic rather
than a hang.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.tmux import HostAttach
from grove.core.workspace import WorkspaceState
from grove.tui.app import GroveApp
from grove.tui.screens.help import HelpScreen
from grove.tui.screens.list import WorkspaceListScreen
from grove.tui.widgets.status import StatusBar
from tests.conftest import FakeTmux

# Bounded so a regression fails instead of hanging the suite. Generous
# relative to the sub-millisecond hop a working implementation needs.
_BLOCK_TIMEOUT_S = 10.0


class _BlockingManager(WorkspaceManager):
    """Manager double whose `create`/`kill` stall until the test releases them.

    Subclasses the real manager (rather than duck-typing it) so a signature
    drift on either verb breaks loudly, and so everything the screen reads
    around the blocked verb — `list`, `peek`, config — is the real thing.

    `dispatched` records every verb the screen actually reached, BEFORE the
    stall, so a de-duplication test can tell "refused" from "still queued".
    """

    def __init__(self, *, repo_root: Path, cfg: GroveConfig, store: JsonWorkspaceStore) -> None:
        super().__init__(repo_root=repo_root, cfg=cfg, store=store)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.released_cleanly = False
        self.dispatched: list[tuple[str, str]] = []
        self._armed = False

    def arm(self) -> None:
        """Start stalling — called after whatever the test seeds up front."""
        self._armed = True

    def _block(self, verb: str, workspace_id: str) -> None:
        if not self._armed:
            return
        self.dispatched.append((verb, workspace_id))
        self.entered.set()
        self.released_cleanly = self.release.wait(timeout=_BLOCK_TIMEOUT_S)

    def create(self, request: CreateWorkspaceRequest) -> WorkspaceState:
        self._block("create", request.title or "")
        return super().create(request)

    def kill(self, workspace_id: str, *, delete_branch: bool | None = None) -> None:
        self._block("kill", workspace_id)
        super().kill(workspace_id, delete_branch=delete_branch)


def _blocking_manager(tmp_repo: Path, tmp_path: Path) -> _BlockingManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            "tmux": {"session_prefix": "test-"},
        }
    )
    return _BlockingManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
    )


async def _await_entered(manager: _BlockingManager) -> None:
    """Yield to the loop until the worker thread is inside the blocked verb."""
    for _ in range(200):
        if manager.entered.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("verb never started on a worker thread")


async def _await_dispatched(manager: _BlockingManager, count: int) -> None:
    """Yield until `count` verbs have reached the manager on worker threads."""
    for _ in range(200):
        if len(manager.dispatched) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"only {len(manager.dispatched)} verbs dispatched, wanted {count}")


@pytest.mark.asyncio
async def test_create_leaves_the_app_responsive_while_in_flight(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A slow `create` (a container provision is minutes of this) must not
    freeze input, and the user must see that work is in flight."""
    del fake_tmux
    manager = _blocking_manager(tmp_repo, tmp_path)
    manager.arm()
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        for ch in "blocking":
            await pilot.press(ch)
        await pilot.press("ctrl+s")
        await pilot.pause()
        await _await_entered(manager)

        # In flight: the user is told so, and the app still takes input.
        assert not manager.release.is_set()
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message == "create in progress…"
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()

        manager.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert manager.released_cleanly is True
        assert [s.title for s in manager.list()] == ["blocking"]
        assert isinstance(app.screen, WorkspaceListScreen)


@pytest.mark.asyncio
async def test_kill_leaves_the_app_responsive_while_in_flight(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The `_safe_call` verbs share one seam — kill stands in for the family."""
    del fake_tmux
    manager = _blocking_manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="doomed"))
    manager.arm()  # seeding done — from here the driven verb stalls

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await _await_entered(manager)

        assert not manager.release.is_set()
        await pilot.press("?")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        await pilot.pause()

        manager.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert manager.released_cleanly is True
        assert manager.list() == []


@pytest.mark.asyncio
async def test_a_failing_verb_flashes_its_error_from_the_worker(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The error surface survives the thread hop: a typed refusal raised on
    the worker still lands as the same error flash it always did."""
    manager = _blocking_manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="gone"))
    fake_tmux.sessions.discard(state.tmux_session)  # vanished externally → OFFLINE

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause()
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message.startswith("message failed:")
        assert bar.flash_level == "error"


@pytest.mark.asyncio
async def test_a_second_verb_on_a_busy_workspace_is_refused_and_says_so(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """One impatient double-press must not be two kills.

    The event loop used to serialize verbs for free; with each one on its own
    worker thread, nothing does. The second press is refused rather than
    queued — and the refusal is visible, because a key that silently does
    nothing is indistinguishable from a broken one.
    """
    del fake_tmux
    manager = _blocking_manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="doomed"))
    manager.arm()

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        for _ in range(2):  # two full kill confirmations, back to back
            await pilot.press("k")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
        await _await_entered(manager)
        await pilot.pause()

        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message == "kill ignored — kill still running on this workspace"
        assert manager.dispatched == [("kill", manager.list()[0].id)]

        manager.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert manager.list() == []


@pytest.mark.asyncio
async def test_the_same_verb_on_another_workspace_still_runs_concurrently(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Serialization is per workspace — a fleet-wide lock would undo the
    whole point of moving verbs onto worker threads."""
    del fake_tmux
    manager = _blocking_manager(tmp_repo, tmp_path)
    first = manager.create(CreateWorkspaceRequest(agent_name="claude", title="one"))
    second = manager.create(CreateWorkspaceRequest(agent_name="claude", title="two"))
    manager.arm()

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await _await_entered(manager)
        # Move to the other row and kill that one while the first still stalls.
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await _await_dispatched(manager, 2)

        assert {wid for _, wid in manager.dispatched} == {first.id, second.id}

        manager.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()


@pytest.mark.asyncio
async def test_quitting_mid_verb_warns_once_before_it_exits(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread worker cannot be cancelled, so exiting waits for it — the
    first `q` says which verb, instead of leaving a silent dead terminal."""
    del fake_tmux
    manager = _blocking_manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="doomed"))
    manager.arm()
    exits: list[bool] = []
    monkeypatch.setattr(GroveApp, "exit", lambda *_a, **_k: exits.append(True))

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("k")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        await _await_entered(manager)

        await pilot.press("q")
        await pilot.pause()
        assert exits == []
        bar = app.screen.query_one(StatusBar)
        assert bar.flash_message == "kill still running — press q again to quit and wait for it"

        await pilot.press("q")
        await pilot.pause()
        assert exits == [True]

        manager.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()


@pytest.mark.asyncio
async def test_ticks_do_no_work_while_the_terminal_is_handed_to_an_attach(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`switch-client` returns immediately and leaves the TUI running in a
    session the user has just left.

    Every tick from that moment repaints a screen nobody can see, and for a
    container workspace the pane tick is a `docker exec` at 4 Hz — spent
    while the user is inside the very workspace being polled. The ticks stay
    off until the user's next input proves they are back.
    """
    del fake_tmux
    manager = _blocking_manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="live"))
    panes: list[str] = []
    lists: list[None] = []
    real_peek_pane, real_list = manager.peek_pane, manager.list

    def _peek_pane(wid: str) -> object:
        panes.append(wid)
        return real_peek_pane(wid)

    def _list() -> list[WorkspaceState]:
        lists.append(None)
        return real_list()

    monkeypatch.setattr(manager, "peek_pane", _peek_pane)
    monkeypatch.setattr(manager, "list", _list)
    monkeypatch.setattr(
        manager,
        "attach",
        lambda _wid: HostAttach(tmux_session=state.tmux_session, inside_outer_tmux=True),
    )
    monkeypatch.setattr("grove.tui.screens.list.subprocess.run", lambda *_a, **_k: None)
    monkeypatch.setattr("grove.tui.screens.list.fit_window_to_client", lambda _s: None)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WorkspaceListScreen)
        # Baseline: with the screen in front of the user, both ticks work.
        screen._refresh_peek()  # seed the cached peek the fast tick splices into
        screen._tick_pane()
        screen._tick_stats()
        assert panes and lists

        screen.action_attach_workspace()
        await pilot.pause()
        before = (len(panes), len(lists))
        screen._tick_pane()
        screen._tick_stats()
        screen._tick_pulse()
        assert (len(panes), len(lists)) == before

        # Any key proves the user is looking again — and the screen snaps
        # current rather than showing however stale the attach left it.
        await pilot.press("j")
        await pilot.pause()
        assert len(lists) > before[1]
        screen._tick_pane()
        assert len(panes) > before[0]
