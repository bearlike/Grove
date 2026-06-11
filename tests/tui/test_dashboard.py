"""DashboardScreen — cross-project activity wall (issue #16).

Pilot tests over a real in-memory store + the FakeTmux seam: opening from the
list screen, grouping by project, the status lens, the agent-state glyphs, and a
poll-driven delta refreshing the wall without a manual reload.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.activity import ActivityService
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.tui.app import GroveApp
from grove.tui.screens.dashboard import DashboardScreen
from grove.tui.widgets.dashboard_grid import DashboardCard, DashboardGrid
from tests.conftest import FakeTmux

pytestmark = pytest.mark.asyncio


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


def _env(tmp_path: Path) -> tuple[RepoRegistry, ActivityService, GroveConfig, JsonWorkspaceStore]:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "t/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    return registry, ActivityService(registry=registry), cfg, store


def _cards(screen: DashboardScreen) -> list[DashboardCard]:
    return list(screen.query(DashboardCard))


async def test_opens_from_list_and_groups_across_repos(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    registry, service, _, _ = _env(tmp_path)
    repo_a = _init_repo(tmp_path / "repo_a")
    repo_b = _init_repo(tmp_path / "repo_b")
    registry.get(repo_a).create(CreateWorkspaceRequest(agent_name="claude", title="a-task"))
    registry.get(repo_b).create(CreateWorkspaceRequest(agent_name="claude", title="b-task"))

    app = GroveApp(registry.get(repo_a))
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(registry.get(repo_a), service=service, registry=registry))
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DashboardScreen)
        cards = _cards(screen)
        # Default lens is "all" — both workspaces show, grouped into two projects.
        assert len(cards) == 2
        titles = {c.activity.state.title for c in cards}
        assert titles == {"a-task", "b-task"}
        headers = [str(s.render()) for s in screen.query(".project-header")]
        assert any("repo_a" in h for h in headers)
        assert any("repo_b" in h for h in headers)


async def test_wall_packs_columns_to_fill_width(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    registry, service, _, _ = _env(tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    for i in range(6):
        mgr.create(CreateWorkspaceRequest(agent_name="claude", title=f"task-{i}"))

    app = GroveApp(mgr)
    async with app.run_test(size=(200, 48)) as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(mgr, service=service, registry=registry))
        await pilot.pause()
        await pilot.pause()
        grid = app.screen.query_one(DashboardGrid)
        # Width-driven packing fills a wide terminal: 200 cells / 36 ≈ 5 columns
        # for six tiles. The old ceil(sqrt(6))=3 left the wall three-fifths empty
        # — the "too spaced out" bug. Guard the floor, not the exact count.
        assert int(grid.styles.grid_size_columns or 0) >= 4


async def test_d_key_opens_dashboard_from_list(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    registry, _, _, _ = _env(tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="task"))

    app = GroveApp(mgr)
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DashboardScreen)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DashboardScreen)


async def test_attention_lens_filters_out_starting(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    registry, service, _, _ = _env(tmp_path)
    repo = _init_repo(tmp_path / "repo")
    registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="task"))

    app = GroveApp(registry.get(repo))
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(registry.get(repo), service=service, registry=registry))
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DashboardScreen)
        assert len(_cards(screen)) == 1  # "all" lens: shown
        # Cycle "all" → "attention": a STARTING session wants nothing, so it drops.
        await pilot.press("l")
        await pilot.pause()
        assert _cards(screen) == []
        assert "attention" in str(screen.sub_title)


async def test_card_shows_agent_state_label(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    del fake_tmux
    registry, service, _, _ = _env(tmp_path)
    repo = _init_repo(tmp_path / "repo")
    registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="task"))

    app = GroveApp(registry.get(repo))
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(registry.get(repo), service=service, registry=registry))
        await pilot.pause()
        await pilot.pause()
        card = _cards(app.screen)[0]
        # No transcript on disk yet → STARTING.
        assert "starting" in card.body_text
        assert "task" in card.body_text


async def test_poll_delta_refreshes_wall(
    fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    registry, service, _, _ = _env(tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="task"))

    app = GroveApp(registry.get(repo))
    async with app.run_test(size=(160, 48)) as pilot:
        await pilot.pause()
        app.push_screen(DashboardScreen(registry.get(repo), service=service, registry=registry))
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, DashboardScreen)
        assert "starting" in _cards(screen)[0].body_text

        # A transcript appears → STARTING becomes WAITING; the poll emits a delta
        # the screen consumes and re-renders, no manual refresh.
        folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
        folder.mkdir(parents=True)
        (folder / f"{state.agent_session_id}.jsonl").write_text(
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
            '{"type":"assistant","uuid":"a1","requestId":"r1",'
            '"timestamp":"2026-06-01T10:00:01.000Z","isSidechain":false,'
            '"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
            '"usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"text","text":"k"}]}}\n',
            encoding="utf-8",
        )
        service.poll_once()
        await pilot.pause()
        await pilot.pause()
        assert "waiting" in _cards(app.screen)[0].body_text
