"""`WorkspaceList.populate` reconciles by id instead of remounting.

The list is repopulated on the screen's slow stats tick forever, so that
out-of-band creates/kills appear without a restart — which means the
no-change case has to be genuinely inert. These tests assert on widget
**identity** — the same `WorkspaceCard` object still mounted — never on
rendered text, because text equality passes just as happily after a full
clear-and-remount, which is exactly the bug.

Driven through a bare one-widget app rather than the full list screen:
`WorkspaceList` owns the reconcile, and a bare app has no `set_interval`
running, so nothing but the test writes the cursor (the parallel-writer
flake documented in `src/grove/tui/CLAUDE.md`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from textual.app import App, ComposeResult

from grove.core import WorkspaceState, WorkspaceStatus
from grove.core.manager import _list_sort_key
from grove.tui.widgets.card import WorkspaceCard
from grove.tui.widgets.list import WorkspaceList

_NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _state(
    wid: str,
    *,
    title: str | None = None,
    status: WorkspaceStatus | None = None,
    minutes_ago: int = 4,
) -> WorkspaceState:
    return WorkspaceState(
        id=wid,
        title=title or f"task-{wid}",
        repo_root="/tmp/repo",
        branch=f"test/{wid}",
        base_branch="main",
        worktree_path=f"/tmp/wt/{wid}",
        tmux_session=f"test-{wid}",
        agent_name="claude",
        status=status or WorkspaceStatus.ACTIVE,
        created_at=_NOW - timedelta(minutes=10),
        updated_at=_NOW - timedelta(minutes=minutes_ago),
    )


class _ListApp(App[None]):
    """Minimal host for the widget under test — no timers, no screen chrome."""

    def compose(self) -> ComposeResult:
        yield WorkspaceList()


def _cards(wlist: WorkspaceList) -> list[WorkspaceCard]:
    return list(wlist.query(WorkspaceCard))


def _identities(wlist: WorkspaceList) -> list[int]:
    return [id(card) for card in _cards(wlist)]


@pytest.mark.asyncio
async def test_tick_with_no_change_mounts_nothing_and_leaves_the_cursor_alone() -> None:
    """The flicker regression guard: a no-change populate must be a no-op.

    Widget identity is the load-bearing assertion — a rebuilt list renders
    identical text but every card is a different object, the highlight is
    genuinely lost, and the scroll position resets.
    """
    states = [_state(f"w{i}") for i in range(20)]
    app = _ListApp()
    async with app.run_test(size=(80, 12)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate(states)
        await pilot.pause()
        wlist.index = 15
        await pilot.pause()

        before = _identities(wlist)
        highlighted = _cards(wlist)[15]
        scroll_before = wlist.scroll_offset

        wlist.populate(list(states))  # the stats tick, nothing changed
        await pilot.pause()

        assert _identities(wlist) == before, "a no-change tick must not remount any card"
        assert wlist.index == 15
        assert _cards(wlist)[15] is highlighted
        assert highlighted.highlighted is True, "the selected row must keep its highlight"
        assert wlist.scroll_offset == scroll_before


@pytest.mark.asyncio
async def test_tick_that_changes_another_row_leaves_the_selection_intact() -> None:
    """A content change elsewhere updates that card in place, not the list."""
    states = [_state("w0"), _state("w1"), _state("w2")]
    app = _ListApp()
    async with app.run_test(size=(80, 20)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate(states)
        await pilot.pause()
        wlist.index = 2
        await pilot.pause()
        before = _identities(wlist)
        selected = _cards(wlist)[2]

        changed = [_state("w0", title="renamed"), _state("w1"), _state("w2")]
        wlist.populate(changed)
        await pilot.pause()

        assert _identities(wlist) == before
        assert "renamed" in _cards(wlist)[0].body_text, "the changed row must repaint in place"
        assert wlist.index == 2
        assert _cards(wlist)[2] is selected
        assert selected.highlighted is True


@pytest.mark.asyncio
async def test_out_of_band_add_mounts_only_the_new_card() -> None:
    """A new id appears in position; every surviving card stays mounted."""
    states = [_state("w0"), _state("w2")]
    app = _ListApp()
    async with app.run_test(size=(80, 20)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate(states)
        await pilot.pause()
        wlist.index = 1
        await pilot.pause()
        survivors = _identities(wlist)

        wlist.populate([_state("w0"), _state("w1"), _state("w2")])
        await pilot.pause()

        cards = _cards(wlist)
        assert [c.workspace_id for c in cards] == ["w0", "w1", "w2"]
        assert [id(c) for c in (cards[0], cards[2])] == survivors
        assert wlist.selected_id == "w2", "the cursor follows its workspace, not its row number"
        assert cards[2].highlighted is True


@pytest.mark.asyncio
async def test_out_of_band_remove_unmounts_only_the_departed_card() -> None:
    states = [_state("w0"), _state("w1"), _state("w2")]
    app = _ListApp()
    async with app.run_test(size=(80, 20)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate(states)
        await pilot.pause()
        wlist.index = 2
        await pilot.pause()
        kept = [id(c) for c in _cards(wlist) if c.workspace_id != "w1"]

        wlist.populate([_state("w0"), _state("w2")])
        await pilot.pause()

        cards = _cards(wlist)
        assert [c.workspace_id for c in cards] == ["w0", "w2"]
        assert [id(c) for c in cards] == kept, "surviving rows must not be remounted"
        assert wlist.selected_id == "w2"


@pytest.mark.asyncio
@pytest.mark.parametrize("flipped", ["w3", "w0"])
async def test_active_to_idle_reorder_moves_cards_without_remounting(flipped: str) -> None:
    """The case the user actually hits: a row goes IDLE and the list re-sorts.

    `manager.list()` sorts by `_list_sort_key` = (status rank, -updated_at)
    and ACTIVE outranks IDLE, so ACTIVE↔IDLE — the most frequent transition
    in the product — reorders the list. If a reorder remounted, the flicker
    would come back exactly when the fleet is busy. The real sort key is
    imported rather than reimplemented, so this pins the actual coupling.

    Two cases: a row flipping *below* the selection (its index is unchanged,
    so the scroll must not move either) and one *above* it (its index
    legitimately changes, so the highlight is asserted by id, never by row).
    """
    states = [_state(f"w{i}", minutes_ago=i) for i in range(6)]
    app = _ListApp()
    async with app.run_test(size=(80, 20)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate(sorted(states, key=_list_sort_key))
        await pilot.pause()
        wlist.index = 1
        await pilot.pause()
        selected_id = wlist.selected_id
        assert selected_id == "w1"
        selected = _cards(wlist)[1]
        before = set(_identities(wlist))
        scroll_before = wlist.scroll_offset

        idle = [
            _state(s.id, minutes_ago=i, status=WorkspaceStatus.IDLE if s.id == flipped else None)
            for i, s in enumerate(states)
        ]
        target = sorted(idle, key=_list_sort_key)
        assert [s.id for s in target] != [s.id for s in sorted(states, key=_list_sort_key)], (
            "the fixture must actually reorder, or this test proves nothing"
        )
        wlist.populate(target)
        await pilot.pause()

        cards = _cards(wlist)
        assert [c.workspace_id for c in cards] == [s.id for s in target]
        assert {id(c) for c in cards} == before, "a reorder must move cards, never remount them"
        assert wlist.selected_id == selected_id, "the cursor follows its workspace, not its row"
        assert cards[wlist.index or 0] is selected
        assert selected.highlighted is True
        if flipped == "w3":  # moved below the selection: its row never changed
            assert wlist.scroll_offset == scroll_before


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "order",
    [
        ["w2", "w0", "w1", "w3"],
        ["w3", "w2", "w1", "w0"],
        ["w1", "w3", "w0", "w2"],
        ["w0", "w1", "w3", "w2"],
    ],
)
async def test_arbitrary_reorder_lands_in_order_with_the_same_widgets(order: list[str]) -> None:
    """`_apply_order` moves the minimal set, so pin the outcome over permutations."""
    states = [_state(f"w{i}") for i in range(4)]
    app = _ListApp()
    async with app.run_test(size=(80, 20)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate(states)
        await pilot.pause()
        before = set(_identities(wlist))

        by_id = {s.id: s for s in states}
        wlist.populate([by_id[wid] for wid in order])
        await pilot.pause()

        cards = _cards(wlist)
        assert [c.workspace_id for c in cards] == order
        assert {id(c) for c in cards} == before


@pytest.mark.asyncio
async def test_filter_keeps_matching_rows_mounted() -> None:
    """Filtering is a membership change, so it rides the same reconcile."""
    app = _ListApp()
    async with app.run_test(size=(80, 20)) as pilot:
        wlist = app.query_one(WorkspaceList)
        wlist.populate([_state("w0", title="alpha"), _state("w1", title="beta")])
        await pilot.pause()
        alpha = _cards(wlist)[0]

        wlist.set_filter("alp")
        await pilot.pause()

        cards = _cards(wlist)
        assert [c.workspace_id for c in cards] == ["w0"]
        assert cards[0] is alpha, "a row that still matches must not be remounted"

        wlist.set_filter("")
        await pilot.pause()
        cards = _cards(wlist)
        assert [c.workspace_id for c in cards] == ["w0", "w1"]
        assert cards[0] is alpha
