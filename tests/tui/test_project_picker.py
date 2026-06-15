"""ProjectPickerScreen — switch repos from within the TUI (issue #59).

Pure tests over ``RepoChoice.group`` (grouping / counts / current presence /
sort) plus Pilot tests over a real in-memory store + the FakeTmux seam: ``P``
opens the picker, the list mirrors the registry, filtering narrows it, and
selecting a repo swaps the running screen's Manager without leaving the app.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.tui.app import GroveApp
from grove.tui.screens.list import WorkspaceListScreen
from grove.tui.screens.project_picker import ProjectPickerScreen, RepoChoice, RepoRow
from grove.tui.widgets.list import WorkspaceList
from tests.conftest import FakeTmux


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@grove.local"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


def _env(tmp_path: Path) -> tuple[RepoRegistry, JsonWorkspaceStore]:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "t/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    # config_loader omitted → every Manager shares this in-memory cfg (hermetic;
    # no real config cascade reaches the test).
    return RepoRegistry(cfg=cfg, store=store), store


# ─── RepoChoice.group (pure) ────────────────────────────────────────────────


def _state(repo_root: Path, wid: str) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=wid,
        title=wid,
        repo_root=str(repo_root.resolve()),
        branch=f"t/{wid}",
        base_branch="main",
        worktree_path=str(repo_root / ".worktrees" / wid),
        tmux_session=f"test-{wid}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
    )


def test_group_counts_and_marks_current(tmp_path: Path) -> None:
    repo_a = tmp_path / "alpha"
    repo_b = tmp_path / "bravo"
    states = [_state(repo_a, "a1"), _state(repo_a, "a2"), _state(repo_b, "b1")]
    choices = RepoChoice.group(states, current=repo_a)
    by_name = {c.name: c for c in choices}
    assert by_name["alpha"].count == 2
    assert by_name["bravo"].count == 1
    assert by_name["alpha"].is_current is True
    assert by_name["bravo"].is_current is False


def test_group_includes_current_even_with_zero_workspaces(tmp_path: Path) -> None:
    repo_a = tmp_path / "alpha"
    repo_b = tmp_path / "bravo"
    # Current repo (bravo) has no persisted workspaces — it must still appear.
    choices = RepoChoice.group([_state(repo_a, "a1")], current=repo_b)
    by_name = {c.name: c for c in choices}
    assert "bravo" in by_name
    assert by_name["bravo"].count == 0
    assert by_name["bravo"].is_current is True


def test_group_includes_declared_known_root_with_zero_workspaces(tmp_path: Path) -> None:
    """A `known` root (registry union, e.g. config-declared) with no workspaces
    still lists — empty projects stay visible in the switcher (#95)."""
    repo_a = tmp_path / "alpha"
    declared = (tmp_path / "delta").resolve()
    choices = RepoChoice.group([_state(repo_a, "a1")], current=repo_a, known=[declared])
    by_name = {c.name: c for c in choices}
    assert "delta" in by_name
    assert by_name["delta"].count == 0
    assert by_name["delta"].is_current is False


def test_group_dedupes_known_root_against_store_and_current(tmp_path: Path) -> None:
    """A root that is store-derived AND in `known` appears once with its count."""
    repo_a = tmp_path / "alpha"
    choices = RepoChoice.group([_state(repo_a, "a1")], current=repo_a, known=[repo_a.resolve()])
    alphas = [c for c in choices if c.name == "alpha"]
    assert len(alphas) == 1
    assert alphas[0].count == 1


def test_group_sorts_current_first_then_by_name(tmp_path: Path) -> None:
    repo_a = tmp_path / "alpha"
    repo_b = tmp_path / "bravo"
    repo_c = tmp_path / "charlie"
    states = [_state(repo_a, "a"), _state(repo_b, "b"), _state(repo_c, "c")]
    # charlie is current → floats first, then alpha, bravo alphabetically.
    names = [c.name for c in RepoChoice.group(states, current=repo_c)]
    assert names == ["charlie", "alpha", "bravo"]


# ─── Pilot flow ─────────────────────────────────────────────────────────────


async def _two_repo_list_screen(
    tmp_path: Path,
) -> tuple[GroveApp, RepoRegistry, Path, Path]:
    registry, _ = _env(tmp_path)
    repo_a = _init_repo(tmp_path / "repo_a")
    repo_b = _init_repo(tmp_path / "repo_b")
    registry.get(repo_a).create(CreateWorkspaceRequest(agent_name="claude", title="a-task"))
    registry.get(repo_b).create(CreateWorkspaceRequest(agent_name="claude", title="b-task"))
    return GroveApp(registry.get(repo_a)), registry, repo_a, repo_b


@pytest.mark.asyncio
async def test_p_opens_picker_listing_every_repo(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    app, registry, repo_a, _ = await _two_repo_list_screen(tmp_path)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        # Push a list screen wired to the injected (hermetic) registry.
        app.push_screen(WorkspaceListScreen(registry.get(repo_a), registry=registry))
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, ProjectPickerScreen)
        names = {row.choice.name for row in picker.query(RepoRow)}
        assert names == {"repo_a", "repo_b"}


@pytest.mark.asyncio
async def test_selecting_a_repo_switches_the_manager(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    app, registry, repo_a, repo_b = await _two_repo_list_screen(tmp_path)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(WorkspaceListScreen(registry.get(repo_a), registry=registry))
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        # Order is current-first: [repo_a (current), repo_b]. Move to repo_b and
        # pick it (Enter on the focused filter selects the highlighted row).
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WorkspaceListScreen)
        assert screen._manager.repo_root.resolve() == repo_b.resolve()
        # The new screen shows the new repo's single workspace.
        titles = {s.title for s in screen.query_one(WorkspaceList).visible_states}
        assert titles == {"b-task"}


@pytest.mark.asyncio
async def test_filter_narrows_the_list(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    app, registry, repo_a, _ = await _two_repo_list_screen(tmp_path)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(WorkspaceListScreen(registry.get(repo_a), registry=registry))
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        for ch in "repo_b":
            await pilot.press(ch)
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, ProjectPickerScreen)
        names = [row.choice.name for row in picker.query(RepoRow)]
        assert names == ["repo_b"]


@pytest.mark.asyncio
async def test_escape_cancels_without_switching(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    app, registry, repo_a, _ = await _two_repo_list_screen(tmp_path)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(WorkspaceListScreen(registry.get(repo_a), registry=registry))
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WorkspaceListScreen)
        assert screen._manager.repo_root.resolve() == repo_a.resolve()


@pytest.mark.asyncio
async def test_selecting_current_repo_is_a_noop(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    app, registry, repo_a, _ = await _two_repo_list_screen(tmp_path)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        original = WorkspaceListScreen(registry.get(repo_a), registry=registry)
        app.push_screen(original)
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        # repo_a is the highlighted (current-first) row; picking it must not
        # switch to a different screen instance.
        await pilot.press("enter")
        await pilot.pause()
        assert app.screen is original
