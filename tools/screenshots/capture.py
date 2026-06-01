"""Render Grove TUI states into reproducible SVG screenshots.

This pipeline runs Grove against a real on-disk demo repository with real
git worktrees and real tmux sessions. The agent process in each workspace
is a small stub script (`tools/screenshots/agents/stub-*.sh`) that prints
realistic content then sleeps, so `tmux capture-pane` returns truthful
pane content rather than empty buffers.

Run via:

    make docs-screenshots

or directly:

    uv run python -m tools.screenshots.capture

Output lands in ``docs/img/screenshots/``. All SVGs are captured at the
same terminal dimensions for visual consistency. The Textual screenshot
mechanism used here is the same one the in-app command palette invokes
(``App.export_screenshot``); driving it from a ``Pilot`` makes the run
deterministic.

Requires `tmux`, `bash`, and `git` on PATH. Cleans up the demo tree on
exit so re-running yields the same SVGs byte-for-byte.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core import tmux as tmux_mod
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import NewNamedBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import TmuxError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.tui.app import GroveApp


# ── monkey-patch create_session so demo panes do not print the user's MOTD
#    Real `grove` invokes `$SHELL` for each new window. On a system with a
#    chatty `.bashrc` (e.g. a server MOTD with system info), capture-pane
#    returns that banner instead of the agent stub's content. Forcing the
#    initial window to spawn `bash --noprofile --norc` keeps the pane
#    clean. Only relevant inside this script.
def _quiet_create_session(name: str, cwd: Path, *, history_limit: int = 50_000) -> None:
    server = tmux_mod._server()
    if tmux_mod.has_session(name):
        raise TmuxError(f"tmux session already exists: {name}")
    try:
        session = server.new_session(
            session_name=name,
            start_directory=str(cwd),
            attach=False,
            window_command="bash --noprofile --norc",
        )
    except Exception as exc:
        raise TmuxError(f"failed to create tmux session {name}: {exc}") from exc
    try:
        session.set_option("history-limit", str(history_limit))
        session.set_option("mouse", "on")
    except Exception:
        pass


tmux_mod.create_session = _quiet_create_session

# Fixed terminal dimensions. 132 columns by 36 rows produces an SVG with
# roughly 16:9 visual aspect once the monospace cell ratio (~0.5:1) is
# applied. Picked so every screenshot embeds at the same size on the
# docs site and the TUI never wraps unexpectedly.
TERMINAL_SIZE = (132, 36)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_ROOT = Path("/tmp/grove-screenshots")
OUT_DIR = REPO_ROOT / "docs" / "img" / "screenshots"
STUB_CLAUDE = REPO_ROOT / "tools" / "screenshots" / "agents" / "stub-claude.sh"
STUB_AIDER = REPO_ROOT / "tools" / "screenshots" / "agents" / "stub-aider.sh"


# ─── git harness ─────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "current")
    _git(repo, "config", "user.email", "demo@grove.local")
    _git(repo, "config", "user.name", "Grove Demo")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-verify")
    return repo.resolve()


def _make_manager(work: Path) -> WorkspaceManager:
    """Build a real-tmux manager rooted at ``work / 'myproject'``.

    The agent registry points at the stub scripts under ``tools/``. The
    rest of the config keeps Grove's shipped defaults so the screenshots
    reflect the out-of-the-box layout.
    """
    repo = _make_repo(work, "myproject")
    # The new-window shell is already non-interactive (see
    # `_quiet_create_session`), so the stub command can be invoked
    # plainly. The build-layout step opens the agent window with the same
    # quiet shell because libtmux inherits the session-level default.
    claude_cmd = str(STUB_CLAUDE)
    aider_cmd = str(STUB_AIDER)
    cfg = GroveConfig.model_validate(
        {
            "tmux": {
                "session_prefix": "grove-",
                "activity_threshold_seconds": 3,
            },
            "agents": [
                {
                    "name": "claude",
                    "command": claude_cmd,
                    "description": "Anthropic Claude Code (stub)",
                },
                {
                    "name": "aider",
                    "command": aider_cmd,
                    "description": "Aider AI pair-programmer (stub)",
                },
                {
                    "name": "shell",
                    "command": "$SHELL",
                    "description": "Plain shell",
                },
            ],
        }
    )
    store = JsonWorkspaceStore(path=work / "state.json")
    return WorkspaceManager(repo_root=repo, cfg=cfg, store=store)


# ─── scenario builders ───────────────────────────────────────────────────────


def _seed_populated(work: Path) -> WorkspaceManager:
    """Build the canonical 'four workspaces in mixed states' scenario.

    Branch names are pinned via ``NewNamedBranch`` to keep the rendered
    output stable across regenerations. The `perf-bench` session is
    killed externally after creation so the reconciler reports it as
    OFFLINE on the next list refresh.
    """
    manager = _make_manager(work)
    # Created in reverse-display order: `manager.list()` returns
    # newest-first, so the last name in this loop ends up at the top of
    # the workspace list and becomes the default selected row. Putting
    # `auth-refactor` last gives the screenshot a populated peek rail
    # (Claude stub content) by default.
    plan = [
        ("perf-bench", "claude", "perf/bench"),
        ("flaky-test-fix", "aider", "fix/flaky-tests"),
        ("docs-rewrite", "claude", "docs/rewrite"),
        ("auth-refactor", "claude", "feat/auth-refactor"),
    ]
    for title, agent, branch in plan:
        manager.create(
            CreateWorkspaceRequest(
                agent_name=agent,
                title=title,
                branch_plan=NewNamedBranch(name=branch),
            )
        )

    # Let the stub agents start, print, and reach `sleep` so capture-pane
    # returns the printed content rather than an empty pane. 3 seconds is
    # generous for a `bash --noprofile --norc -c` stub on any reasonable
    # machine.
    time.sleep(3)
    perf_state = next(s for s in manager.list() if s.title == "perf-bench")
    tmux_mod.kill_session(perf_state.tmux_session)
    return manager


def _seed_empty(work: Path) -> WorkspaceManager:
    return _make_manager(work)


def _teardown(manager: WorkspaceManager) -> None:
    """Kill every tmux session this manager owns, ignoring failures."""
    for state in manager.list():
        try:
            if tmux_mod.has_session(state.tmux_session):
                tmux_mod.kill_session(state.tmux_session)
        except Exception:  # noqa: BLE001 — best effort cleanup
            pass


# ─── pilot runner ────────────────────────────────────────────────────────────


async def _shoot(
    manager: WorkspaceManager,
    name: str,
    title: str,
    actions: Callable[[Any], Awaitable[None]] | None = None,
) -> None:
    app = GroveApp(manager)
    async with app.run_test(size=TERMINAL_SIZE) as pilot:
        # The peek rail refreshes its agent card on a 0.25s tick. Wait
        # a couple of ticks plus a small margin so the cached peek gets
        # populated and the widget repaints with real pane content.
        await pilot.pause(0.6)
        if actions is not None:
            await actions(pilot)
            await pilot.pause(0.3)
        svg = app.export_screenshot(title=title)
        (OUT_DIR / f"{name}.svg").write_text(svg, encoding="utf-8")


# ─── entry point ─────────────────────────────────────────────────────────────


async def main() -> None:
    # Drop loguru's default DEBUG sink; the run is otherwise noisy.
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if DEMO_ROOT.exists():
        shutil.rmtree(DEMO_ROOT)
    DEMO_ROOT.mkdir(parents=True)

    populated = _seed_populated(DEMO_ROOT / "populated")
    try:
        # NOTE: the workspace-list and create-modal views are documented with
        # real PNG captures (docs/img/screenshots/tui-list.png and
        # tui-create-modal.png), so they are intentionally not auto-rendered
        # here. The populated scenario is still the backdrop for the
        # filter / kill / pause / help captures below.
        async def _open_help(pilot: Any) -> None:
            await pilot.press("question_mark")
            await pilot.pause()

        await _shoot(populated, "tui-help", "Grove · help", _open_help)

        async def _filter(pilot: Any) -> None:
            await pilot.press("slash")
            await pilot.pause()
            for ch in "auth":
                await pilot.press(ch)

        await _shoot(populated, "tui-filter", "Grove · filter", _filter)

        async def _kill_confirm(pilot: Any) -> None:
            await pilot.press("k")
            await pilot.pause()

        await _shoot(populated, "tui-kill-confirm", "Grove · kill confirm", _kill_confirm)

        async def _pause_confirm(pilot: Any) -> None:
            await pilot.press("p")
            await pilot.pause()

        await _shoot(populated, "tui-pause-confirm", "Grove · pause confirm", _pause_confirm)
    finally:
        _teardown(populated)

    empty = _seed_empty(DEMO_ROOT / "empty")
    try:
        await _shoot(empty, "tui-empty", "Grove · empty state")
    finally:
        _teardown(empty)

    shutil.rmtree(DEMO_ROOT, ignore_errors=True)
    print(f"wrote SVG screenshots to {OUT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
