"""The ``TaskBoard`` reconstruction — Claude Code's Task system.

Anthropic split the single ``TodoWrite`` call into ``TaskCreate``-per-item +
``TaskUpdate``-per-change (``TaskList``/``TaskGet`` are read-only queries of the
same board and stay unrecognized; ``TaskOutput``/``TaskStop`` are a different,
background-process concept entirely — verified against the tools' own on-host
descriptions). Unlike ``TodoList.from_tool_call`` (pure, one call → one list),
folding Task calls needs a running accumulator, so this pins ``TaskBoard`` in
isolation; the adapter-level wiring (where the reconstructed board rides the
turn stream) is pinned in ``test_claude_code.py``.

Payloads mirror the REAL on-host shapes: ``TaskCreate`` carries
``subject``/``description``/``activeForm``; ``TaskUpdate`` carries ``taskId``
plus whichever of ``status``/``subject``/``description``/``activeForm``/
``owner``/``addBlocks``/``addBlockedBy`` changed; a ``TaskCreate``'s own
``tool_result`` reads ``"Task #<n> created successfully: <subject>"`` — the
only place the assigned id appears.
"""

from __future__ import annotations

from grove.core.agents import TASK_TOOL_NAMES, TaskBoard, TodoItem
from grove.core.agents.model import ContentBlock


def test_created_task_id_parses_the_confirmation_text() -> None:
    """The assigned id lives only in the ``tool_result`` text — pin the exact
    on-host confirmation shape."""
    assert TaskBoard.created_task_id("Task #3 created successfully: Fix the bug") == "3"
    assert TaskBoard.created_task_id("Task #1 created successfully: x") == "1"


def test_created_task_id_degrades_on_missing_or_unexpected_result() -> None:
    """No result yet (call in flight), or a result that doesn't match the
    known confirmation shape, returns ``None`` — never guesses an id."""
    assert TaskBoard.created_task_id(None) is None
    assert TaskBoard.created_task_id("") is None
    assert TaskBoard.created_task_id("Something else entirely") is None


def test_create_adds_a_pending_item_in_order() -> None:
    board = TaskBoard()
    assert board.create("1", {"subject": "Fix the bug", "activeForm": "Fixing the bug"})
    assert board.create("2", {"subject": "Write the test"})
    assert board.snapshot().items == (
        TodoItem(content="Fix the bug", status="pending", active_form="Fixing the bug"),
        TodoItem(content="Write the test", status="pending", active_form=None),
    )


def test_create_with_unreadable_subject_is_a_no_op() -> None:
    board = TaskBoard()
    assert board.create("1", {"description": "no subject here"}) is False
    assert board.create("2", {"subject": "   "}) is False
    assert board.snapshot().items == ()


def test_update_moves_status_without_touching_other_fields() -> None:
    board = TaskBoard()
    board.create("1", {"subject": "Fix the bug", "activeForm": "Fixing the bug"})
    assert board.update("1", {"taskId": "1", "status": "in_progress"})
    (item,) = board.snapshot().items
    assert item == TodoItem(
        content="Fix the bug", status="in_progress", active_form="Fixing the bug"
    )


def test_update_can_change_subject_and_active_form_together() -> None:
    board = TaskBoard()
    board.create("1", {"subject": "Draft"})
    assert board.update(
        "1", {"taskId": "1", "subject": "Fix the bug", "activeForm": "Fixing the bug"}
    )
    (item,) = board.snapshot().items
    assert item.content == "Fix the bug"
    assert item.active_form == "Fixing the bug"


def test_update_ignores_fields_this_board_does_not_model() -> None:
    """``owner``/``addBlocks``/``addBlockedBy`` ride the real wire but have no
    ``TodoItem`` equivalent — a status-only change still applies; an update
    carrying ONLY unmodeled fields is a no-op (nothing readable changed)."""
    board = TaskBoard()
    board.create("1", {"subject": "Fix the bug"})
    assert board.update("1", {"taskId": "1", "status": "in_progress", "owner": "alice"})
    assert board.snapshot().items[0].status == "in_progress"
    assert board.update("2", {"taskId": "2"}) is False
    assert not board.update("1", {"taskId": "1", "owner": "bob"})


def test_update_to_untracked_id_is_a_no_op() -> None:
    """A ``TaskUpdate`` for an id this board never saw ``TaskCreate`` resolve
    for (e.g. the create call's result was unparseable) degrades quietly —
    the caller falls back to the generic tool entry rather than fabricating a
    task."""
    board = TaskBoard()
    board.create("1", {"subject": "Fix the bug"})
    assert board.update("99", {"taskId": "99", "status": "completed"}) is False
    assert len(board.snapshot().items) == 1


def test_update_with_unrecognized_status_is_ignored_not_coerced() -> None:
    """Unlike ``TodoList.from_tool_call`` (which coerces an unknown status to
    ``pending`` on CREATE), an update carrying an unrecognized status simply
    doesn't move the existing status — coercing it would silently regress an
    in-progress/completed task back to pending on a provider quirk."""
    board = TaskBoard()
    board.create("1", {"subject": "Fix the bug"})
    board.update("1", {"taskId": "1", "status": "in_progress"})
    assert board.update("1", {"taskId": "1", "status": "blocked"}) is False
    assert board.snapshot().items[0].status == "in_progress"


def test_snapshot_keeps_creation_order_across_updates() -> None:
    """A task is never reordered by an update — only its fields move, so the
    card doesn't visually jump around as the agent works through the list."""
    board = TaskBoard()
    board.create("1", {"subject": "first"})
    board.create("2", {"subject": "second"})
    board.create("3", {"subject": "third"})
    board.update("1", {"taskId": "1", "status": "completed"})
    assert [i.content for i in board.snapshot().items] == ["first", "second", "third"]


def test_task_tool_names_covers_exactly_create_and_update() -> None:
    """The recognizer is deliberately narrow: ``TaskList``/``TaskGet`` are
    read-only queries of the same board (no new state to fold), and
    ``TaskOutput``/``TaskStop`` are a different concept (background shell/agent
    process output + cancellation) — neither belongs here."""
    assert sorted(TASK_TOOL_NAMES) == ["TaskCreate", "TaskUpdate"]


# ─── apply() — the one dispatch seam every caller shares ───────────────────
#
# ``apply`` is the create-vs-update dispatch previously private to the Claude
# adapter (`_apply_task_call`); it moved onto `TaskBoard` itself so the
# `latest_todo` projection (`model.latest_todo_from_messages`) can fold the
# SAME logic `claude_code.py::_assistant_entries` uses, instead of a second
# copy. Behavior is unchanged from the adapter-level tests in
# `test_claude_code.py` — these pin the seam directly.


def test_apply_taskcreate_resolves_the_id_from_its_own_tool_result() -> None:
    board = TaskBoard()
    answered = {"tc1": "Task #1 created successfully: Fix the bug"}
    block = ContentBlock(
        type="tool_use",
        tool_name="TaskCreate",
        tool_use_id="tc1",
        tool_input={"subject": "Fix the bug"},
    )
    assert board.apply("TaskCreate", block, answered) is True
    assert board.snapshot().items[0].content == "Fix the bug"


def test_apply_taskcreate_without_a_resolved_result_is_a_no_op() -> None:
    board = TaskBoard()
    block = ContentBlock(
        type="tool_use", tool_name="TaskCreate", tool_use_id="tc1", tool_input={"subject": "x"}
    )
    assert board.apply("TaskCreate", block, {}) is False
    assert board.snapshot().items == ()


def test_apply_taskupdate_dispatches_to_update() -> None:
    board = TaskBoard()
    board.create("1", {"subject": "Fix the bug"})
    block = ContentBlock(
        type="tool_use",
        tool_name="TaskUpdate",
        tool_use_id="tu1",
        tool_input={"taskId": "1", "status": "in_progress"},
    )
    assert board.apply("TaskUpdate", block, {}) is True
    assert board.snapshot().items[0].status == "in_progress"


def test_apply_with_a_non_dict_payload_is_a_no_op() -> None:
    board = TaskBoard()
    block = ContentBlock(
        type="tool_use", tool_name="TaskCreate", tool_use_id="tc1", tool_input=None
    )
    assert board.apply("TaskCreate", block, {}) is False
