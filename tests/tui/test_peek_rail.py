"""Pilot tests for the peek rail wiring.

Pin the contract:
- The rail mounts and reflects the selected workspace.
- Cursor highlights schedule a peek recompute (debounced).
- The rail collapses (display: none) below the narrow threshold.
- A pure-rendering helper produces stable markup for known peek payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from rich.style import Style
from textual.widgets import Static

from grove.core import (
    CommitSummary,
    InitStatus,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)
from grove.core.agents import AgentActivity, AgentActivityState
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.tui._status import agent_state_color, chrome_color, ref_color, status_color
from grove.tui.app import GroveApp
from grove.tui.widgets.card import WorkspaceCard
from grove.tui.widgets.list import WorkspaceList
from grove.tui.widgets.peek_rail import (
    PeekRail,
    _agent_line,
    _render_pane_body,
    _render_peek,
    _render_workspace,
)
from tests.conftest import FakeTmux


def _manager(tmp_repo: Path, tmp_path: Path) -> WorkspaceManager:
    cfg = GroveConfig.model_validate(
        {
            "worktree": {
                "root_template": str(tmp_path / "trees"),
                "branch_prefix": "test/",
            },
            # Tighten the peek timers so timing-sensitive tests don't pay
            # production cadences. Production defaults are 0.25/3.0; tests
            # use 0.1/0.5 so a 2.5 s pilot.pause spans many ticks.
            "tmux": {
                "session_prefix": "test-",
                "agent_window_name": "agent",
                "peek_pane_refresh_seconds": 0.1,
                "peek_stats_refresh_seconds": 0.5,
            },
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


def _stub_state(**overrides: object) -> WorkspaceState:
    base = {
        "id": "wid-1",
        "title": "demo",
        "repo_root": "/tmp/repo",
        "branch": "grove/demo-1",
        "base_branch": "main",
        "worktree_path": "/tmp/wt",
        "tmux_session": "grove-demo-1",
        "agent_name": "claude",
        # Default ACTIVE so renderers that gate on LIVE_STATUSES still show
        # the pane card. Tests for non-live statuses override.
        "status": WorkspaceStatus.ACTIVE,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return WorkspaceState(**base)  # type: ignore[arg-type]


# ─── pure rendering ──────────────────────────────────────────────────────────


def test_render_peek_includes_description_when_set() -> None:
    """The rail shows the user's description when one is set — placed
    right after the stats line so it reads as the primary "what is this
    workspace for" affordance once the stats are scanned."""
    peek = WorkspacePeek(
        state=_stub_state(description="see ticket #1234"),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )
    body = _render_peek(peek).plain
    assert "see ticket #1234" in body


def test_render_peek_omits_description_when_empty() -> None:
    """An empty description renders nothing — no `(no description)`
    placeholder. It would be visual noise on every workspace."""
    peek = WorkspacePeek(
        state=_stub_state(description=None),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )
    body = _render_peek(peek).plain
    assert "no description" not in body.lower()


def test_render_peek_trims_long_description() -> None:
    """Long descriptions trim with `…` so a paste doesn't hijack the
    rail's vertical budget. Trim threshold is 200 chars."""
    long_desc = "x" * 500
    peek = WorkspacePeek(
        state=_stub_state(description=long_desc),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )
    body = _render_peek(peek).plain
    assert "…" in body
    # Ellipsis falls within the configured trim window.
    assert "x" * 500 not in body


def test_render_peek_running_includes_live_stats() -> None:
    """The rail's first card carries live git stats now that the workspace
    card list owns the title/status/branch header. Title and branch must
    NOT appear in the rail body — they would duplicate what the focused
    card already shows."""
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=2,
        base_behind=0,
        diff_added=10,
        diff_removed=3,
        dirty_files=1,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    body = _render_peek(peek).plain

    # Title + branch live on the WorkspaceCard now; rail must not duplicate.
    assert "demo" not in body
    assert "grove/demo-1" not in body
    assert "+10" in body and "-3" in body
    assert "ahead" in body and "behind" in body
    assert "dirty" in body
    # Muted-dot separators give the stats line its scan-able rhythm.
    # The ratio (3 dots = 4 stat groups) pins the layout intent rather than
    # exact whitespace; a future column rearrangement that drops a group
    # without updating the test would surface here.
    assert body.count("·") >= 3


def test_render_peek_stats_are_muted_when_zero() -> None:
    """`ahead 0` / `behind 0` / `dirty 0` all render in muted hex — the
    user reads "no signal here" and moves on. No bold, no semantic color."""
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    text = _render_workspace(peek, dark=True)
    muted = chrome_color("muted", dark=True).lower()
    for label in ("ahead", "behind", "dirty"):
        seen = False
        for start, end, style in text.spans:
            if text.plain[start:end] == label:
                style_str = str(style).lower()
                # Zero-state labels are muted with no bold.
                assert muted in style_str, (
                    f"'{label}' (zero) span should be muted hex {muted}; got {style_str}"
                )
                assert "bold" not in style_str, (
                    f"'{label}' (zero) span should not be bold; got {style_str}"
                )
                seen = True
                break
        assert seen, f"expected a '{label}' span"


def test_render_peek_stats_promote_to_semantic_color_when_nonzero() -> None:
    """`ahead N>0` promotes to green (ref-add); `behind N>0` and `dirty N>0`
    promote to amber (ORPHANED warn). Polarity is what the eye actually
    needs at a glance: 'is there work to push? to pull? to clean?' The
    label *and* value share the polarity hue so they read as one chunk.

    `_ref` / `_status` aliasing kept the imports tidy when this test was
    drafted; the canonical accessors are imported at module top.
    """
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=3,
        base_behind=1,
        diff_added=5,
        diff_removed=2,
        dirty_files=4,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    text = _render_workspace(peek, dark=True)
    green = ref_color("diff_add", dark=True).lower()
    amber = status_color(WorkspaceStatus.ORPHANED, dark=True).lower()

    expected = {
        "ahead": green,
        "behind": amber,
        "dirty": amber,
    }
    for label, color in expected.items():
        # Both the label and the trailing value chunk should share the hue.
        label_styled = False
        for start, end, style in text.spans:
            if text.plain[start:end] == label and color in str(style).lower():
                label_styled = True
                break
        assert label_styled, (
            f"non-zero '{label}' label should be styled in {color}; spans seen: "
            f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
        )


def test_render_peek_stats_use_muted_separator_color() -> None:
    """The ``·`` glyphs between stat groups carry the muted hex from
    `chrome_color('muted')` — same source of truth the footer uses for its
    separator. Pinning the *color span* (not just the character) means a
    future refactor that drops the muted styling on the dot would fail
    here even if the character survives.
    """
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    text = _render_workspace(peek, dark=True)
    muted = chrome_color("muted", dark=True).lower()
    found = False
    for start, end, style in text.spans:
        if "·" in text.plain[start:end] and muted in str(style).lower():
            found = True
            break
    assert found, (
        f"expected at least one '·' span styled with {muted}; spans seen: "
        f"{[(text.plain[s:e], str(st)) for s, e, st in text.spans]}"
    )


def test_agent_line_renders_model_counters_tokens_and_state() -> None:
    """The agent-metrics line carries model (agent hue), `12t/34r/87⚒`
    counters (bold default fg), humanized tokens, and the state label in
    its agent-state color."""
    agent = AgentActivity(
        state=AgentActivityState.WORKING,
        model="claude-opus-4",
        human_turns=12,
        assistant_replies=34,
        tool_calls=87,
        tokens_in=412_000,
        tokens_out=38_000,
    )
    text = _agent_line(agent, dark=True)
    plain = text.plain
    assert "claude-opus-4" in plain
    assert "12t/34r/87⚒" in plain
    assert "412.0k↑ 38.0k↓" in plain  # _human_tokens — same formatter as the dashboard
    assert "working" in plain

    info_hex = ref_color("info", dark=True).lower()
    state_hex = agent_state_color(AgentActivityState.WORKING, dark=True).lower()
    styles = {plain[s:e]: str(st).lower() for s, e, st in text.spans}
    assert info_hex in styles["claude-opus-4"]
    assert styles["12t/34r/87⚒"] == "bold"  # neutral counter: bold, no color
    assert "bold" in styles["working"] and state_hex in styles["working"]


def test_agent_line_skips_model_and_tokens_when_absent() -> None:
    """No model / zero tokens → those segments are skipped, not blank-filled.
    The counters and the state label always render."""
    agent = AgentActivity(state=AgentActivityState.WAITING)
    plain = _agent_line(agent, dark=True).plain
    assert "0t/0r/0⚒" in plain
    assert "waiting" in plain
    assert "↑" not in plain and "↓" not in plain


def test_render_workspace_places_agent_line_between_stats_and_description() -> None:
    """The agent-metrics line sits between the stats line and the
    description block (summary-card content order, position 2)."""
    peek = WorkspacePeek(
        state=_stub_state(description="see ticket #1234"),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )
    agent = AgentActivity(state=AgentActivityState.WORKING, human_turns=3)
    lines = _render_workspace(peek, dark=True, agent=agent).plain.splitlines()
    assert "dirty" in lines[0]  # stats first
    assert "3t/0r/0⚒" in lines[1]  # agent metrics second
    assert any("see ticket #1234" in line for line in lines[2:])  # description after


def test_render_workspace_without_agent_is_byte_identical() -> None:
    """Regression pin: ``agent=None`` (and the omitted default) renders the
    exact same bytes as before the agent axis existed — the decomposition
    into block helpers is a pure refactor for agent-less callers."""
    peek = WorkspacePeek(
        state=_stub_state(description="note", status=WorkspaceStatus.PAUSED),
        base_ahead=2,
        base_behind=1,
        diff_added=10,
        diff_removed=3,
        dirty_files=4,
        recent_commits=(
            CommitSummary(
                sha="abcdef12",
                subject="add widget",
                committed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )
    default = _render_workspace(peek, dark=True)
    explicit = _render_workspace(peek, dark=True, agent=None)
    assert default.plain == explicit.plain
    assert default.spans == explicit.spans
    # No agent-axis token leaks into the agent-less render.
    assert "⚒" not in default.plain


def test_render_peek_paused_workspace_shows_resume_hint() -> None:
    peek = WorkspacePeek(
        state=_stub_state(status=WorkspaceStatus.PAUSED),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    body = _render_peek(peek).plain

    assert "paused" in body
    assert "resume" in body.lower()


def test_render_peek_init_failed_shows_log_path() -> None:
    peek = WorkspacePeek(
        state=_stub_state(
            init_status=InitStatus.FAILED,
            init_log_path="/tmp/grove/logs/wid-1-init.log",
        ),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    body = _render_peek(peek).plain

    assert "init failed" in body
    assert "/tmp/grove/logs/wid-1-init.log" in body


def test_render_peek_includes_recent_commits_and_pane() -> None:
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(
            CommitSummary(
                sha="abcdef12",
                subject="add widget",
                committed_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        agent_snapshot="line1\nline2\nline3",
        snapshot_taken_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    body = _render_peek(peek).plain

    assert "abcdef12" in body
    assert "add widget" in body
    assert "line3" in body  # the tail-trim must keep recent lines


def test_render_peek_running_with_empty_pane_shows_silent_marker() -> None:
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=None,
        snapshot_taken_at=None,
    )

    body = _render_peek(peek).plain

    assert "no output" in body  # silent-pane affordance


def test_render_peek_decodes_ansi_escapes_in_pane_block() -> None:
    """capture-pane -e emits SGR escapes; the rail must parse them into
    Rich style spans. Raw ESC bytes leaking into the rendered body would
    surface as garbage in the user's terminal."""
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot="\x1b[31mhello\x1b[0m\nworld",
        snapshot_taken_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    text = _render_peek(peek)
    plain = text.plain

    assert "hello" in plain
    assert "world" in plain
    assert "\x1b" not in plain  # raw ESC must not leak into the body


def test_render_pane_body_strips_bgcolor_so_panel_surface_shows_through() -> None:
    """SGR bg codes from the captured tmux snapshot must NOT paint cell
    backgrounds; the card's `$surface` has to show through.

    Why: `tmux capture-pane -e` carries the agent terminal's own bg color
    in its SGR cells. When rendered into a Static on the panel surface,
    those bgs paint a darker rectangle behind every glyph and visually
    fight the panel's own bg. Strip bg per span; preserve fg + style
    attributes so `[31m` red stays red, etc."""
    snapshot = "\x1b[31;44mred-on-blue\x1b[0m\nplain"
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=snapshot,
        snapshot_taken_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    pane = _render_pane_body(peek)
    styled_spans = [s for s in pane.spans if isinstance(s.style, Style)]
    assert styled_spans, "expected at least one styled span from the SGR snapshot"
    for span in styled_spans:
        assert isinstance(span.style, Style)  # narrowing for the type checker
        assert span.style.bgcolor is None, (
            f"span {pane.plain[span.start : span.end]!r} kept bgcolor "
            f"{span.style.bgcolor}; expected None so card surface shows through"
        )
    # Fg attributes must survive the strip so the snapshot still reads as
    # styled output, not plain text.
    assert any(isinstance(s.style, Style) and s.style.color is not None for s in styled_spans), (
        "fg color was stripped along with bg — only bg should be cleared"
    )


def test_render_pane_body_disables_wrap_so_long_lines_clip() -> None:
    """Lines wider than the pane card must clip, never wrap. Wrapping
    visually breaks lazygit / claude-code grid alignment and looks awful.
    Setting ``Text.no_wrap = True`` lets Rich crop at the card width
    without wrapping; combined with not resizing the source tmux pane,
    this is the whole story for fit-content-into-viewport."""
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot="x" * 200,
        snapshot_taken_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    pane = _render_pane_body(peek)

    assert pane.no_wrap is True


def test_render_peek_keeps_last_30_pane_lines() -> None:
    """The rail caps the displayed pane at 30 lines (was 12 — too small to
    feel like a live mirror). Capture upstream caps at 60; this is the
    second filter so the rail stays bounded regardless of caller."""
    snapshot = "\n".join(f"line{n}" for n in range(50))
    peek = WorkspacePeek(
        state=_stub_state(),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        recent_commits=(),
        agent_snapshot=snapshot,
        snapshot_taken_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    plain = _render_peek(peek).plain

    assert "line49" in plain  # newest preserved
    assert "line20" in plain  # 50 - 30 = 20, oldest within window
    assert "line19" not in plain  # one before the window


# ─── live wiring (Pilot) ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rail_renders_for_selected_workspace(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Selecting a workspace must recompute the rail (live stats present,
    not the empty placeholder) and surface the workspace's title/branch
    on the focused WorkspaceCard. Title + branch live on the card list
    now, not the rail body."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="demo"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        # Selection should land on the only row → rail recomputes for it.
        await pilot.pause(delay=0.2)

        # Rail carries live stats now (`ahead`, `behind`, `dirty`), no
        # longer the title/branch — those live on the WorkspaceCard.
        rail = app.screen.query_one(PeekRail)
        text = rail.body_text
        assert "(no workspace selected)" not in text
        assert "ahead" in text and "dirty" in text

        # The focused card body must carry title + branch.
        wlist = app.screen.query_one(WorkspaceList)
        assert wlist.selected_id == state.id

        cards = wlist.query(WorkspaceCard)
        assert len(cards) == 1
        assert state.title in cards[0].body_text
        assert state.branch in cards[0].body_text

        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_set_peek_with_agent_surfaces_metrics_in_body(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """`set_peek(peek, agent=...)` renders the agent-metrics line into the
    workspace card; the keyword is optional so agent-less callers (and the
    pre-existing tests) keep their exact output."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Freeze the screen's own peek ticks so the manual set_peek below is
        # the only writer (same discipline as the pulse-timer lesson).
        for attr in ("_stats_timer", "_pane_timer", "_pulse_timer"):
            timer = getattr(screen, attr)
            assert timer is not None
            timer.stop()
        rail = screen.query_one(PeekRail)
        peek = WorkspacePeek(
            state=_stub_state(),
            base_ahead=0,
            base_behind=0,
            diff_added=0,
            diff_removed=0,
            dirty_files=0,
            recent_commits=(),
            agent_snapshot=None,
            snapshot_taken_at=None,
        )
        agent = AgentActivity(
            state=AgentActivityState.WORKING,
            model="claude-opus-4",
            human_turns=12,
            assistant_replies=34,
            tool_calls=87,
        )
        rail.set_peek(peek, agent=agent)
        await pilot.pause()
        assert "12t/34r/87⚒" in rail.body_text
        assert "claude-opus-4" in rail.body_text
        assert "working" in rail.body_text
        # And clearing the agent drops the line again.
        rail.set_peek(peek)
        await pilot.pause()
        assert "12t/34r/87⚒" not in rail.body_text
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_empty_when_no_workspaces(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        rail = app.screen.query_one(PeekRail)
        text = rail.body_text
        assert "(no workspace selected)" in text
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_collapses_below_narrow_threshold(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="demo"))
    app = GroveApp(manager)
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert screen.has_class("-narrow")
        rail = screen.query_one(PeekRail)
        # Textual hides via display: none; the rail's `display` style is "none".
        assert rail.styles.display == "none"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_visible_above_narrow_threshold(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="demo"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        assert not screen.has_class("-narrow")
        rail = screen.query_one(PeekRail)
        assert rail.styles.display != "none"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_auto_refreshes_for_hovered_selection(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rail keeps the hovered selection's snapshot fresh without polling
    tmux on every keystroke. Spy on manager.peek to count how often the
    auto-refresh interval ticks, then verify the count climbs while the
    list screen is active."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="autotick"))

    real_peek = manager.peek
    calls = {"count": 0}

    def _spy(workspace_id: str) -> object:
        calls["count"] += 1
        return real_peek(workspace_id)

    monkeypatch.setattr(manager, "peek", _spy)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.3)
        baseline = calls["count"]
        # Advance past two auto-refresh windows; expect ≥ 1 extra call.
        await pilot.pause(delay=2.5)
        assert calls["count"] > baseline
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_auto_refresh_is_frozen_while_modal_is_open(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While a modal is up, peek shouldn't be recomputed — the rail is
    behind the modal and the user is interacting with the modal, so any
    git/tmux work is wasted and shows up as input lag."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="frozen"))

    real_peek = manager.peek
    calls = {"count": 0}

    def _spy(workspace_id: str) -> object:
        calls["count"] += 1
        return real_peek(workspace_id)

    monkeypatch.setattr(manager, "peek", _spy)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.3)
        # Open the create modal — list screen is no longer the top screen.
        await pilot.press("n")
        await pilot.pause()
        baseline = calls["count"]
        # Wait past one full auto-refresh window; the count must not climb.
        await pilot.pause(delay=2.5)
        assert calls["count"] == baseline
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_fast_pane_tick_polls_peek_pane(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fast pane-tick (cfg.peek_pane_refresh_seconds, ~0.1 s in tests)
    polls peek_pane independently of the slow stats-tick. Spy on peek_pane
    to verify it fires."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="fast"))
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "frame-1"

    real = manager.peek_pane
    pane_calls = {"count": 0}

    def _spy(workspace_id: str) -> object:
        pane_calls["count"] += 1
        return real(workspace_id)

    monkeypatch.setattr(manager, "peek_pane", _spy)

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.6)  # past one stats tick → cache primed
        baseline = pane_calls["count"]
        await pilot.pause(delay=0.6)
        assert pane_calls["count"] > baseline
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_rail_fast_tick_updates_pane_block_when_snapshot_changes(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Fast tick splices fresh snapshot text into the cached peek without
    waiting for the slow stats tick. Updating the FakeTmux snapshot should
    surface in the rail's body within one fast-tick window."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="splice"))
    target = f"{state.tmux_session}:agent"
    fake_tmux.snapshots[target] = "frame-one"

    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.6)
        rail = app.screen.query_one(PeekRail)
        assert "frame-one" in rail.body_text

        # Mutate the captured pane between ticks; fast tick must pick it up.
        fake_tmux.snapshots[target] = "frame-two"
        await pilot.pause(delay=0.4)
        assert "frame-two" in rail.body_text
        await pilot.press("q")
        await pilot.pause()


# ─── two-card structure (workspace metadata + live pane) ────────────────────


@pytest.mark.asyncio
async def test_rail_has_workspace_and_pane_cards(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Structural seam: rail composes a workspace card and a pane card,
    each with the shared `.grove-card` class. Renaming or removing either
    breaks this loudly so we don't silently lose the visual boundary."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="cards"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        rail = app.screen.query_one(PeekRail)
        ws_card = rail.query_one("#card-workspace", Static)
        pane_card = rail.query_one("#card-pane", Static)
        assert ws_card.has_class("grove-card")
        assert pane_card.has_class("grove-card")
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_panel_titles_are_unique(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """The three panels visible on the list screen — the WorkspaceList on
    the left, and PeekRail's two cards on the right — carry distinct
    border titles. An earlier revision shipped 'workspace' next to
    'workspaces' which the eye reads as a typo; pin against that
    regression. The trio is `workspaces · summary · agent`."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    manager.create(CreateWorkspaceRequest(agent_name="claude", title="x"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        wlist = app.screen.query_one(WorkspaceList)
        rail = app.screen.query_one(PeekRail)
        ws_card = rail.query_one("#card-workspace", Static)
        pane_card = rail.query_one("#card-pane", Static)
        titles = {
            str(wlist.border_title),
            str(ws_card.border_title),
            str(pane_card.border_title),
        }
        assert len(titles) == 3, f"expected three distinct panel titles; got {sorted(titles)}"
        # Pin the actual names — moving titles around without updating
        # docs / muscle memory is itself a regression.
        assert str(wlist.border_title) == "workspaces"
        assert str(ws_card.border_title) == "summary"
        assert str(pane_card.border_title) == "Live Workspace Preview"
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_pane_card_is_live_when_running(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Brand-language: the live pane card carries `-live` so its border
    swaps to `$primary` (clay). The workspace card never gets `-live`
    — it describes state, it doesn't carry attention."""
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="live"))
    fake_tmux.snapshots[f"{state.tmux_session}:agent"] = "live work"
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.2)
        rail = app.screen.query_one(PeekRail)
        pane_card = rail.query_one("#card-pane", Static)
        ws_card = rail.query_one("#card-workspace", Static)
        assert pane_card.has_class("-live")
        assert not pane_card.has_class("-hidden")
        assert not ws_card.has_class("-live")
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_pane_card_hidden_when_paused(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Workspace not RUNNING → pane card hidden (no `-live`, gets `-hidden`).
    The workspace card still carries the paused affordance, so we don't
    duplicate placeholder text — the hidden pane card just gives the
    workspace card more vertical room."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="paused"))
    manager.pause(state.id)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.2)
        rail = app.screen.query_one(PeekRail)
        pane_card = rail.query_one("#card-pane", Static)
        assert pane_card.has_class("-hidden")
        assert not pane_card.has_class("-live")
        # And the workspace card still surfaces the resume affordance.
        assert "resume" in rail.body_text.lower()
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_pane_card_hidden_when_no_peek(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """No peek (no workspaces) → workspace card shows the empty placeholder
    and the pane card is fully hidden."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        rail = app.screen.query_one(PeekRail)
        pane_card = rail.query_one("#card-pane", Static)
        assert pane_card.has_class("-hidden")
        assert "(no workspace selected)" in rail.body_text
        await pilot.press("q")
        await pilot.pause()


@pytest.mark.asyncio
async def test_cursor_move_updates_rail(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Cursor move past the debounce window must shift selection on the
    list and re-render the rail. The card list carries the per-row
    title; the rail keeps live stats. Pinning both confirms the
    selection-driven peek wiring is intact across the swap."""
    del fake_tmux
    manager = _manager(tmp_repo, tmp_path)
    s1 = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alpha"))
    s2 = manager.create(CreateWorkspaceRequest(agent_name="claude", title="beta"))
    app = GroveApp(manager)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.2)
        rail = app.screen.query_one(PeekRail)
        wlist = app.screen.query_one(WorkspaceList)

        # Force selection to the first card deterministically; sort order
        # depends on creation timestamps and isn't part of this test.
        wlist.jump_to(0)
        await pilot.pause(delay=0.2)
        first_id = wlist.selected_id
        first_rail = rail.body_text

        # Move to the next card, wait past the debounce window.
        await pilot.press("down")
        await pilot.pause(delay=0.2)
        second_id = wlist.selected_id
        second_rail = rail.body_text

        # Cursor moved between the two creation siblings.
        assert first_id != second_id
        assert {first_id, second_id} == {s1.id, s2.id}
        # Rail re-rendered (no empty placeholder) for both selections.
        assert "(no workspace selected)" not in first_rail
        assert "(no workspace selected)" not in second_rail
        await pilot.press("q")
        await pilot.pause()
