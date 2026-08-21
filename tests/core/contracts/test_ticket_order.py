"""The cross-client ordering contract for attached ticket references."""

from __future__ import annotations

from grove.core.contracts import ticket_sort_key
from grove.core.phase import TaskPhase


def _sort(*tickets: tuple[str, str, TaskPhase | None, str]) -> list[str]:
    ordered = sorted(tickets, key=lambda ticket: ticket_sort_key(*ticket))
    return [ticket_id for *_, ticket_id in ordered]


def test_pull_requests_sort_before_issues() -> None:
    assert _sort(("issue", "open", None, "2"), ("pull_request", "open", None, "1")) == ["1", "2"]


def test_state_order_is_open_draft_unknown_then_settled() -> None:
    assert _sort(
        ("issue", "merged", None, "5"),
        ("issue", "closed", None, "4"),
        ("issue", "unknown", None, "3"),
        ("issue", "draft", None, "2"),
        ("issue", "open", None, "1"),
    ) == ["1", "2", "3", "4", "5"]


def test_higher_phase_sorts_before_lower_phase() -> None:
    assert _sort(("issue", "open", "planning", "1"), ("issue", "open", "delivering", "2")) == [
        "2",
        "1",
    ]


def test_done_sorts_after_other_claimed_phases() -> None:
    assert _sort(("issue", "open", "done", "1"), ("issue", "open", "delivering", "2")) == [
        "2",
        "1",
    ]


def test_unclaimed_ticket_sorts_after_claimed_ticket() -> None:
    assert _sort(("issue", "open", None, "1"), ("issue", "open", "scoping", "2")) == ["2", "1"]


def test_ticket_id_tiebreak_is_repeatably_stable() -> None:
    tickets = [
        ("issue", "open", "implementing", "10"),
        ("issue", "open", "implementing", "2"),
        ("issue", "open", "implementing", "ENG-1"),
    ]
    first = sorted(tickets, key=lambda ticket: ticket_sort_key(*ticket))
    assert [ticket[-1] for ticket in first] == ["2", "10", "ENG-1"]
    assert sorted(first, key=lambda ticket: ticket_sort_key(*ticket)) == first
