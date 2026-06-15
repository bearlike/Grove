"""SessionsScreen — per-workspace session-history browser (issue #33).

Pilot tests: opening from the list screen on `s` (real engine path — no
transcripts → empty state), the no-selection guard, and row/turns rendering
over a duck-typed explorer fake (the screen consumes only `for_workspace`
and `turns_for`, the engine seams built in #28).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core import SessionListing
from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    DigestEntry,
    SessionSummary,
    SessionTurn,
)
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.tui.app import GroveApp
from grove.tui.screens.list import WorkspaceListScreen
from grove.tui.screens.sessions import SessionList, SessionRow, SessionsScreen
from grove.tui.widgets.footer import ContextualFooter
from tests.conftest import FakeTmux

pytestmark = pytest.mark.asyncio


def _manager(tmp_repo: Path, tmp_path: Path) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _summary(
    session_id: str,
    *,
    title: str | None = None,
    prompt: str | None = None,
    turns: int = 3,
) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        adapter_kind="claude_code",
        transcript_path=Path(f"/tmp/transcripts/{session_id}.jsonl"),
        cwd="/tmp/worktrees/alpha",
        created_at=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        modified_at=datetime(2026, 6, 11, 9, 0, tzinfo=UTC),
        size_bytes=1024,
        title=title,
        first_prompt=prompt,
        last_prompt=prompt,
        activity=AgentActivity(state=AgentActivityState.WAITING, human_turns=turns),
    )


def _listing(session_id: str, **kwargs: object) -> SessionListing:
    return SessionListing(
        summary=_summary(session_id, **kwargs),  # type: ignore[arg-type]
        provenance="fs_discovered",
        workspace_id="w1",
        workspace_title="alpha",
        workspace_branch="grove/alpha",
    )


def _turn(prompt: str, *, reply: str = "done", tool: str | None = None) -> SessionTurn:
    entries: list[DigestEntry] = []
    if tool is not None:
        entries.append(DigestEntry(role="tool", text=tool))
    entries.append(DigestEntry(role="assistant", text=reply))
    return SessionTurn(
        user_text=prompt,
        started_at=datetime(2026, 6, 11, 8, 30, tzinfo=UTC),
        entries=tuple(entries),
    )


class _FakeExplorer:
    """Duck-typed SessionExplorer: only the two read methods the screen consumes."""

    def __init__(
        self,
        listings: list[SessionListing],
        turns_by_id: dict[str, tuple[SessionTurn, ...]],
    ) -> None:
        self._listings = tuple(listings)
        self._turns = dict(turns_by_id)
        self.turns_calls = 0

    def for_workspace(self, workspace_id: str) -> tuple[SessionListing, ...]:
        del workspace_id
        return self._listings

    def turns_for(
        self, listing: SessionListing, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        del last
        self.turns_calls += 1
        return self._turns.get(listing.summary.session_id, ())


async def test_s_opens_sessions_screen_and_escape_pops(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Real engine path: a fresh workspace has no transcripts on disk, so the
    screen opens straight into the empty state instead of crashing."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SessionsScreen)
        assert screen.has_class("-empty")
        assert str(screen.sub_title) == "alpha"
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, WorkspaceListScreen)


async def test_s_with_no_selection_stays_on_list(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert isinstance(app.screen, WorkspaceListScreen)


async def test_rows_render_and_first_session_turns_show(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    explorer = _FakeExplorer(
        [
            _listing("aaaa1111-0000", title="fix the parser"),
            _listing("bbbb2222-0000", prompt="write the docs"),
        ],
        {
            "aaaa1111-0000": (
                _turn("fix the parser please", reply="patched it", tool="Edit parser.py"),
            ),
            "bbbb2222-0000": (_turn("write the docs"),),
        },
    )
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            SessionsScreen(explorer, workspace_id="w1", workspace_title="alpha")  # type: ignore[arg-type]
        )
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SessionsScreen)
        rows = list(screen.query(SessionRow))
        assert len(rows) == 2
        assert "aaaa1111" in rows[0].body_text
        assert "claude_code" in rows[0].body_text
        assert "fix the parser" in rows[0].body_text
        # Newest (first) session's turns render with the CLI glyph conventions.
        # Tool calls are GROUPED by default — one muted "N tool calls" row,
        # never the individual ⚒ lines (press `t` to expand those).
        text = screen.turns_text
        assert "fix the parser please" in text
        assert "❯" in text  # noqa: RUF001 — the CLI's deliberate prompt glyph
        assert "⚒ 1 tool call" in text
        assert "Edit parser.py" not in text
        # The agent speaker label sits on its own line above the markdown reply.
        assert "agent ⏺" in text
        assert "patched it" in text


async def test_highlight_change_loads_turns_for_next_session(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    explorer = _FakeExplorer(
        [_listing("aaaa1111-0000"), _listing("bbbb2222-0000")],
        {
            "aaaa1111-0000": (_turn("first-session prompt"),),
            "bbbb2222-0000": (_turn("second-session prompt"),),
        },
    )
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            SessionsScreen(explorer, workspace_id="w1", workspace_title="alpha")  # type: ignore[arg-type]
        )
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SessionsScreen)
        assert "first-session prompt" in screen.turns_text
        await pilot.press("down")
        await pilot.pause()
        assert "second-session prompt" in screen.turns_text
        # Moving back re-renders from the per-session cache — no second parse.
        calls_before = explorer.turns_calls
        await pilot.press("up")
        await pilot.pause()
        assert "first-session prompt" in screen.turns_text
        assert explorer.turns_calls == calls_before


async def test_t_toggles_tool_call_detail(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`t` expands the grouped tool rows into individual ⚒ lines and back.
    The footer carries the binding like every other screen key."""
    del fake_tmux
    explorer = _FakeExplorer(
        [_listing("aaaa1111-0000")],
        {
            "aaaa1111-0000": (
                SessionTurn(
                    user_text="run the suite",
                    started_at=datetime(2026, 6, 11, 8, 30, tzinfo=UTC),
                    entries=(
                        DigestEntry(role="tool", text="Bash pytest"),
                        DigestEntry(role="tool", text="Read report.txt"),
                        DigestEntry(role="assistant", text="all green"),
                    ),
                ),
            ),
        },
    )
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            SessionsScreen(explorer, workspace_id="w1", workspace_title="alpha")  # type: ignore[arg-type]
        )
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SessionsScreen)
        # Grouped by default.
        assert "⚒ 2 tool calls" in screen.turns_text
        assert "Bash pytest" not in screen.turns_text
        # The contextual footer surfaces the binding.
        footer_rendered = str(screen.query_one(ContextualFooter).render())
        assert "t" in footer_rendered and "Tools" in footer_rendered
        # `t` expands to the individual ⚒ lines …
        await pilot.press("t")
        await pilot.pause()
        assert "⚒ Bash pytest" in screen.turns_text
        assert "⚒ Read report.txt" in screen.turns_text
        assert "tool calls" not in screen.turns_text
        # … and toggles back to the grouped row.
        await pilot.press("t")
        await pilot.pause()
        assert "⚒ 2 tool calls" in screen.turns_text
        assert "Bash pytest" not in screen.turns_text


async def test_panel_titles_are_role_nouns(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`sessions` and `history` — unique role-noun titles per the design system."""
    del fake_tmux
    explorer = _FakeExplorer([_listing("aaaa1111-0000")], {})
    app = GroveApp(_manager(tmp_repo, tmp_path))
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        app.push_screen(
            SessionsScreen(explorer, workspace_id="w1", workspace_title="alpha")  # type: ignore[arg-type]
        )
        await pilot.pause()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, SessionsScreen)
        titles = {
            str(screen.query_one(SessionList).border_title),
            str(screen.query_one("#history-panel").border_title),
        }
        assert titles == {"sessions", "history"}
