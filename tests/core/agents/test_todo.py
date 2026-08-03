"""The provider-neutral ``TodoList`` contract + its one normalizer.

These pin the *shape* every adapter normalizes to and the single classmethod that
maps a native ``TodoWrite`` / ``update_plan`` tool call onto it — never the
semantics of any one provider. Payloads mirror the REAL on-host shapes: Claude
``TodoWrite`` carries ``input.todos[]`` with ``content``/``status``/``activeForm``;
Codex ``update_plan`` carries ``plan[]`` with ``step``/``status`` (verified against
a real ``~/.codex`` rollout, codex 0.114 — no ``content``, no ``activeForm``). The
adapter-level wiring (where the todo rides the turn stream) is pinned in each
adapter's own test module; here we pin the contract in isolation.
"""

from __future__ import annotations

from grove.core.agents import TODO_TOOL_NAMES, TodoItem, TodoList


def test_claude_todowrite_normalizes_each_item() -> None:
    """A Claude ``TodoWrite`` (``todos[]`` with ``content``/``status``/
    ``activeForm``) yields ONE list carrying each item in order — the todo
    analogue of a batched question yielding N rows, except a todo write is one
    list, so ``from_tool_call`` returns a 1-tuple."""
    (todo,) = TodoList.from_tool_call(
        "TodoWrite",
        {
            "todos": [
                {"content": "Read the code", "status": "completed", "activeForm": "Reading"},
                {"content": "Write the fix", "status": "in_progress", "activeForm": "Writing"},
                {"content": "Run the gates", "status": "pending", "activeForm": "Running"},
            ]
        },
    )
    assert todo.items == (
        TodoItem(content="Read the code", status="completed", active_form="Reading"),
        TodoItem(content="Write the fix", status="in_progress", active_form="Writing"),
        TodoItem(content="Run the gates", status="pending", active_form="Running"),
    )


def test_codex_update_plan_maps_step_to_content() -> None:
    """A Codex ``update_plan`` uses ``plan[]`` with ``step`` (not ``content``) and
    carries no ``activeForm`` — the shape verified against a real on-host rollout.
    ``step`` maps faithfully to ``content``; ``active_form`` stays ``None``."""
    (todo,) = TodoList.from_tool_call(
        "update_plan",
        {
            "explanation": "Plan to refactor",
            "plan": [
                {"step": "Review current orchestration code", "status": "completed"},
                {"step": "Design minimal refactor", "status": "in_progress"},
                {"step": "Implement and test", "status": "pending"},
            ],
        },
    )
    assert [(i.content, i.status, i.active_form) for i in todo.items] == [
        ("Review current orchestration code", "completed", None),
        ("Design minimal refactor", "in_progress", None),
        ("Implement and test", "pending", None),
    ]


def test_unrecognized_status_coerces_to_pending() -> None:
    """A status outside the normalized triple coerces to ``"pending"`` — shape
    normalization (the safe "not started" default), never guessing intent, never
    raising."""
    (todo,) = TodoList.from_tool_call(
        "TodoWrite", {"todos": [{"content": "x", "status": "blocked"}]}
    )
    assert todo.items[0].status == "pending"
    # A missing status is the same defensive path.
    (todo2,) = TodoList.from_tool_call("TodoWrite", {"todos": [{"content": "y"}]})
    assert todo2.items[0].status == "pending"


def test_items_without_task_text_are_dropped() -> None:
    """An item with neither ``content`` nor ``step`` (or a blank one) is skipped;
    the readable items around it survive."""
    (todo,) = TodoList.from_tool_call(
        "TodoWrite",
        {
            "todos": [
                {"content": "keep me", "status": "pending"},
                {"status": "pending"},
                {"content": "   ", "status": "pending"},
                {"content": "and me", "status": "completed"},
            ]
        },
    )
    assert [i.content for i in todo.items] == ["keep me", "and me"]


def test_non_todo_tool_yields_nothing() -> None:
    """A tool that isn't a todo writer normalizes to ``()`` — the caller falls
    through to the generic tool digest."""
    assert TodoList.from_tool_call("Bash", {"command": "ls"}) == ()


def test_malformed_input_degrades_to_empty_never_raises() -> None:
    """A recognized name with a junk payload returns ``()`` rather than raising,
    the same defensive posture as ``FileEdit`` / ``AgentQuestion`` — a provider
    quirk degrades to the generic digest, never breaks the render loop."""
    assert TodoList.from_tool_call("TodoWrite", None) == ()
    assert TodoList.from_tool_call("TodoWrite", {"todos": "not a list"}) == ()
    assert TodoList.from_tool_call("update_plan", {"plan": 42}) == ()
    # A well-formed call whose items are all unreadable → () (nothing to render).
    assert TodoList.from_tool_call("TodoWrite", {"todos": [{}, {"status": "pending"}]}) == ()
    # An empty list is a cleared todo — no card, so () (degrade to nothing).
    assert TodoList.from_tool_call("TodoWrite", {"todos": []}) == ()


def test_recognizes_returns_true_only_for_todo_tools() -> None:
    """``recognizes`` is the single predicate every adapter shares — exactly the
    two known todo writers."""
    assert TodoList.recognizes("TodoWrite")
    assert TodoList.recognizes("update_plan")
    assert not TodoList.recognizes("Edit")
    assert not TodoList.recognizes("AskUserQuestion")
    assert sorted(TODO_TOOL_NAMES) == ["TodoWrite", "update_plan"]


def test_summary_reads_progress_and_active_item() -> None:
    """The digest ``summary`` a role-unaware consumer reads: completed/total plus
    the in-progress item when there is one."""
    (todo,) = TodoList.from_tool_call(
        "TodoWrite",
        {
            "todos": [
                {"content": "a", "status": "completed"},
                {"content": "b", "status": "in_progress"},
                {"content": "c", "status": "pending"},
            ]
        },
    )
    assert todo.summary == "1/3 done · b"
    # All settled → no active item, just the count.
    (done,) = TodoList.from_tool_call(
        "TodoWrite", {"todos": [{"content": "a", "status": "completed"}]}
    )
    assert done.summary == "1/1 done"
