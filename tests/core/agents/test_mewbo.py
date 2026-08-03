"""MewboAdapter + MewboClient over a mock HTTP transport.

Fakes sit at the HTTP boundary only (``httpx.MockTransport``) — the client,
the event parsing, and the activity mapping all run for real. Payload shapes
mirror the wire contract: ``GET /events`` → ``{events: [{ts, type, payload}],
status, done_reason, title, running}``; ``GET /agents`` → token rollup with
PEAK-input semantics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from grove.core.agents.mewbo import MewboAdapter
from grove.core.agents.model import AgentActivityState, FileEdit, TodoItem
from grove.core.config import MewboConfig
from grove.core.errors import MewboError
from grove.core.mewbo import MewboClient

CWD = Path("/tmp/grove-trees/fix-flaky-test")

# A realistic two-turn session: plan → tool step (call + result) → llm rollup
# → steering reply → second user turn → completion. The llm_call_end inputs
# (1200 then 2400) make peak (2400) differ from the cumulative sum (3600), so
# a peak-vs-billed mixup fails loudly in the token assertions.
EVENTS_RUNNING: dict[str, Any] = {
    "status": "running",
    "done_reason": None,
    "title": "Fix the flaky test",
    "running": True,
    "events": [
        {
            "ts": "2026-06-11T10:00:00+00:00",
            "type": "user",
            "payload": {"text": "Fix the flaky test in CI"},
        },
        {
            "ts": "2026-06-11T10:00:01+00:00",
            "type": "action_plan",
            "payload": {
                "steps": [
                    {"title": "Reproduce locally", "description": "run the suite"},
                    {"title": "Patch the race", "description": "lock the fixture"},
                ]
            },
        },
        {
            "ts": "2026-06-11T10:00:02+00:00",
            "type": "tool",
            "payload": {"tool_id": "shell", "operation": "run", "tool_input": "pytest -x"},
        },
        {
            # Result-side twin of the call above: carries tool_id too, but must
            # not double the step count or steal current_task.
            "ts": "2026-06-11T10:00:03+00:00",
            "type": "tool_result",
            "payload": {"tool_id": "shell", "operation": "run", "result": "1 failed"},
        },
        {
            "ts": "2026-06-11T10:00:04+00:00",
            "type": "llm_call_end",
            "payload": {
                "model": "anthropic/claude-sonnet-4-6",
                "input_tokens": 1200,
                "output_tokens": 300,
            },
        },
        {
            "ts": "2026-06-11T10:00:05+00:00",
            "type": "agent_message",
            "payload": {"text": "Reproduced; patching now."},
        },
        {
            "ts": "2026-06-11T10:00:06+00:00",
            "type": "user",
            "payload": {"text": "Also update the docs"},
        },
        {
            "ts": "2026-06-11T10:00:07+00:00",
            "type": "llm_call_end",
            "payload": {
                "model": "anthropic/claude-sonnet-4-6",
                "input_tokens": 2400,
                "output_tokens": 500,
            },
        },
        {
            "ts": "2026-06-11T10:00:08+00:00",
            "type": "completion",
            "payload": {"text": "Done.", "done_reason": "completed"},
        },
    ],
}

# /agents rollup: total_input_tokens is PEAK (root peak + sub peaks), NOT the
# billed cumulative sum — that lives on /usage as total_input_tokens_billed.
AGENTS_ROLLUP: dict[str, Any] = {
    "agents": [],
    "running": True,
    "total_steps": 7,
    "total_input_tokens": 2600,
    "total_output_tokens": 900,
}


def _transport(
    *,
    events: dict[str, Any] | None = None,
    agents: dict[str, Any] | None = None,
    sessions: dict[str, Any] | None = None,
    create: dict[str, Any] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/events") and events is not None:
            return httpx.Response(200, json=events)
        if path.endswith("/agents") and agents is not None:
            return httpx.Response(200, json=agents)
        if path == "/api/sessions" and request.method == "GET" and sessions is not None:
            return httpx.Response(200, json=sessions)
        if path == "/api/sessions" and request.method == "POST" and create is not None:
            return httpx.Response(200, json=create)
        return httpx.Response(404, json={"error": {"code": 404, "reason": "not found"}})

    return httpx.MockTransport(handler)


def _adapter(transport: httpx.MockTransport) -> MewboAdapter:
    return MewboAdapter(client=MewboClient(MewboConfig(), transport=transport))


# ─── activity mapping ────────────────────────────────────────────────────────


def test_parse_activity_full_mapping() -> None:
    adapter = _adapter(_transport(events=EVENTS_RUNNING, agents=AGENTS_ROLLUP))
    activity = adapter.parse_activity(CWD, "sess-1")

    assert activity.state is AgentActivityState.WORKING  # authoritative status
    assert activity.title == "Fix the flaky test"
    # Turn counts come from user-event boundaries; agent_message + completion
    # are the assistant replies.
    assert activity.human_turns == 2
    assert activity.replies_per_turn == (1, 1)
    assert activity.assistant_replies == 2
    # current_task is the latest action_plan step title or tool call — here
    # the tool call postdates the plan.
    assert activity.current_task == "shell: run"
    assert activity.model == "anthropic/claude-sonnet-4-6"
    assert activity.last_event_at == datetime(2026, 6, 11, 10, 0, 8, tzinfo=UTC)
    # Rollup wins: PEAK input (2600), never the billed-style cumulative sum
    # (3600 across the llm_call_end events).
    assert activity.tokens_in == 2600
    assert activity.tokens_out == 900
    assert activity.tool_calls == 7  # rollup total_steps


def test_parse_activity_tokens_fall_back_to_event_peak_when_agents_unavailable() -> None:
    adapter = _adapter(_transport(events=EVENTS_RUNNING))  # /agents → 404
    activity = adapter.parse_activity(CWD, "sess-1")

    # Same peak-not-sum semantics computed from llm_call_end events.
    assert activity.tokens_in == 2400
    assert activity.tokens_out == 800
    assert activity.tool_calls == 1  # call-side tool events only, result not doubled


@pytest.mark.parametrize(
    ("status", "done_reason", "expected"),
    [
        ("running", None, AgentActivityState.WORKING),
        ("completed", "completed", AgentActivityState.WAITING),
        ("interrupted", None, AgentActivityState.WAITING),
        ("waiting_user", None, AgentActivityState.BLOCKED),
        ("needs_input", None, AgentActivityState.BLOCKED),
        ("failed", None, AgentActivityState.ERROR),
        # done_reason refines a terminal status: "completed" with an
        # error-ish reason is an error, not a clean finish.
        ("completed", "error: model exploded", AgentActivityState.ERROR),
        (None, None, AgentActivityState.UNKNOWN),
        ("brand-new-status", None, AgentActivityState.UNKNOWN),
    ],
)
def test_status_mapping(
    status: str | None, done_reason: str | None, expected: AgentActivityState
) -> None:
    payload = {"status": status, "done_reason": done_reason, "events": []}
    adapter = _adapter(_transport(events=payload))
    assert adapter.parse_activity(CWD, "sess-1").state is expected


def test_failed_status_surfaces_error_detail_from_done_reason() -> None:
    payload = {"status": "failed", "done_reason": "tool_crash", "events": []}
    adapter = _adapter(_transport(events=payload))
    activity = adapter.parse_activity(CWD, "sess-1")
    assert activity.state is AgentActivityState.ERROR
    assert activity.error_detail == "tool_crash"


# ─── typed final-result extraction ───────────────────────────────────────────


def test_final_result_reflects_authoritative_status_not_the_raw_event_type() -> None:
    """``EVENTS_RUNNING``'s last assistant-authored event is a ``completion``
    record, but the top-level status is still ``running`` — Mewbo's own
    AUTHORITATIVE-status rule (module docstring) means ``is_complete`` tracks
    ``status``, never the incidental fact the tail event looks terminal."""
    adapter = _adapter(_transport(events=EVENTS_RUNNING))
    result = adapter.final_result(CWD, "sess-1")
    assert result is not None
    assert result.text == "Done."
    assert result.is_complete is False


def test_final_result_complete_when_status_settles() -> None:
    payload = {**EVENTS_RUNNING, "status": "completed", "done_reason": "completed"}
    adapter = _adapter(_transport(events=payload))
    result = adapter.final_result(CWD, "sess-1")
    assert result is not None
    assert result.is_complete is True
    assert result.text == "Done."


def test_final_result_none_when_no_assistant_reply_yet() -> None:
    payload = {
        "status": "running",
        "done_reason": None,
        "events": [
            {"ts": "2026-06-11T10:00:00+00:00", "type": "user", "payload": {"text": "go"}},
        ],
    }
    adapter = _adapter(_transport(events=payload))
    assert adapter.final_result(CWD, "sess-1") is None


# ─── latest-todo projection ──────────────────────────────────────────────────


def test_latest_todo_reads_the_last_recognized_todo_event() -> None:
    """Mewbo has no message spine, so the projection is over the SAME
    ``_turn_entries`` fold ``read_turns`` already builds — a second
    ``TodoWrite`` overwrites the first."""
    events = {
        "status": "running",
        "events": [
            {
                "ts": "2026-07-08T10:00:00+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "plan-1",
                    "operation": "TodoWrite",
                    "tool_input": {"todos": [{"content": "Scope it", "status": "completed"}]},
                },
            },
            {
                "ts": "2026-07-08T10:00:01+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "plan-2",
                    "operation": "TodoWrite",
                    "tool_input": {
                        "todos": [
                            {"content": "Scope it", "status": "completed"},
                            {"content": "Ship it", "status": "in_progress"},
                        ]
                    },
                },
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    todo = adapter.latest_todo(CWD, "sess-1")
    assert todo is not None
    assert todo.items == (
        TodoItem(content="Scope it", status="completed"),
        TodoItem(content="Ship it", status="in_progress"),
    )


def test_latest_todo_none_when_no_todo_event() -> None:
    adapter = _adapter(_transport(events=EVENTS_RUNNING))
    assert adapter.latest_todo(CWD, "sess-1") is None


def test_latest_todo_none_when_events_fetch_fails() -> None:
    adapter = _adapter(_transport())  # /events 404s
    assert adapter.latest_todo(CWD, "sess-1") is None


def test_latest_task_is_uncapped_where_current_task_is_capped() -> None:
    """The remote adapter owes the same two-reader answer the filesystem ones
    do: ``current_task`` truncated for the ~1 Hz delta, the whole text behind
    the per-request seam, both naming the SAME text."""
    long_text = ("Investigate the flaky fixture and write it up. " * 20).strip()
    assert len(long_text) > 500
    events = {
        "status": "running",
        "done_reason": None,
        "title": "Long brief",
        "running": True,
        "events": [
            {"ts": "2026-07-08T10:00:00+00:00", "type": "user", "payload": {"text": long_text}}
        ],
    }
    adapter = _adapter(_transport(events=events))

    full = adapter.latest_task(CWD, "sess-1")
    capped = adapter.parse_activity(CWD, "sess-1").current_task

    assert full == long_text
    assert capped is not None
    assert len(capped) <= 500
    assert capped.endswith("…")
    assert long_text.startswith(capped[:-1].rstrip())


def test_latest_task_none_when_events_fetch_fails() -> None:
    adapter = _adapter(_transport())  # /events 404s
    assert adapter.latest_task(CWD, "sess-1") is None


def test_final_result_none_when_events_fetch_fails() -> None:
    adapter = _adapter(_transport())  # no /events route registered → 404
    assert adapter.final_result(CWD, "sess-1") is None


# ─── read_turns / digest ─────────────────────────────────────────────────────


def test_read_turns_shape() -> None:
    adapter = _adapter(_transport(events=EVENTS_RUNNING, agents=AGENTS_ROLLUP))
    turns = adapter.read_turns(CWD, "sess-1")

    assert len(turns) == 2
    assert turns[0].user_text == "Fix the flaky test in CI"
    assert turns[0].started_at == datetime(2026, 6, 11, 10, 0, 0, tzinfo=UTC)
    roles = [entry.role for entry in turns[0].entries]
    # plan, tool CALL (with input), tool RESULT (its own entry), reply — in order.
    assert roles == ["status", "tool", "tool", "assistant"]
    # The plan entry now carries each step's description, not just its title.
    assert turns[0].entries[0].text == (
        "plan: Reproduce locally: run the suite; Patch the race: lock the fixture"
    )
    # The call entry carries the label AND the input it ran (string tool_input).
    assert turns[0].entries[1].text == "shell: run pytest -x"
    # The result event renders as its own entry — the call+result pair, like Claude.
    assert turns[0].entries[2].text == "1 failed"
    assert turns[1].user_text == "Also update the docs"
    assert [entry.text for entry in turns[1].entries] == ["Done."]


def test_read_turns_last_window_and_leading_continuation() -> None:
    events = {
        "status": "completed",
        "events": [
            # Assistant content before any user event (a re-engaged session):
            # collects under a leading turn with empty user_text, never dropped.
            {
                "ts": "2026-06-11T09:00:00+00:00",
                "type": "agent_message",
                "payload": {"text": "Picking the task back up."},
            },
            {
                "ts": "2026-06-11T09:00:01+00:00",
                "type": "user",
                "payload": {"text": "Continue please"},
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    turns = adapter.read_turns(CWD, "sess-1")
    assert len(turns) == 2
    assert turns[0].user_text == ""
    assert turns[0].entries[0].text == "Picking the task back up."

    assert len(adapter.read_turns(CWD, "sess-1", last=1)) == 1
    assert adapter.read_turns(CWD, "sess-1", last=0) == ()


def test_transcript_digest_orders_and_truncates() -> None:
    adapter = _adapter(_transport(events=EVENTS_RUNNING))
    digest = adapter.transcript_digest(CWD, "sess-1")
    # user, plan-status, tool CALL, tool RESULT — the call+result pair now both
    # appear, each clamped to the digest cap.
    assert [entry.role for entry in digest.entries[:4]] == ["user", "status", "tool", "tool"]
    assert digest.entries[2].text == "shell: run pytest -x"
    assert digest.entries[3].text == "1 failed"


# A dict-shaped tool_input (the common case on the live /events payloads) plus
# a result event whose error field is the part worth surfacing. The two
# llm_call_end inputs keep peak (1500) distinct from the sum (2000).
EVENTS_DICT_INPUT: dict[str, Any] = {
    "status": "running",
    "done_reason": None,
    "title": "Read the wiki",
    "events": [
        {
            "ts": "2026-06-12T10:00:00+00:00",
            "type": "user",
            "payload": {"text": "Look up the README"},
        },
        {
            "ts": "2026-06-12T10:00:01+00:00",
            "type": "tool",
            "payload": {
                "tool_id": "wiki_read_file",
                "operation": "get",
                "tool_input": {"path": "README.md", "start_line": 1, "end_line": 55},
            },
        },
        {
            # Result twin: success false, the error is the useful text (preferred
            # over an empty/absent result body).
            "ts": "2026-06-12T10:00:02+00:00",
            "type": "tool_result",
            "payload": {
                "tool_id": "wiki_read_file",
                "operation": "get",
                "result": None,
                "error": "Permission denied for wiki_read_file.",
            },
        },
        {
            "ts": "2026-06-12T10:00:03+00:00",
            "type": "llm_call_end",
            "payload": {"input_tokens": 1500, "output_tokens": 600},
        },
        {
            "ts": "2026-06-12T10:00:04+00:00",
            "type": "llm_call_end",
            "payload": {"input_tokens": 1000, "output_tokens": 400},
        },
        {
            "ts": "2026-06-12T10:00:05+00:00",
            "type": "completion",
            "payload": {"text": "Couldn't read it."},
        },
    ],
}


def test_read_turns_renders_dict_input_and_error_result() -> None:
    adapter = _adapter(_transport(events=EVENTS_DICT_INPUT))
    turns = adapter.read_turns(CWD, "sess-1")

    assert len(turns) == 1
    roles = [entry.role for entry in turns[0].entries]
    assert roles == ["tool", "tool", "assistant"]  # call+input, result, reply
    # A dict tool_input flattens to k=v pairs; None values drop out.
    assert turns[0].entries[0].text == (
        "wiki_read_file: get path='README.md' start_line=1 end_line=55"
    )
    # The result entry prefers the error text over the empty result body.
    assert turns[0].entries[1].text == "Permission denied for wiki_read_file."


def test_enriched_rendering_leaves_metrics_unchanged() -> None:
    """The richer entries must not move any activity metric — counting stays
    call-side only (the result event renders but is never tallied)."""
    adapter = _adapter(_transport(events=EVENTS_DICT_INPUT))  # no /agents → fallback
    activity = adapter.parse_activity(CWD, "sess-1")

    assert activity.state is AgentActivityState.WORKING
    assert activity.human_turns == 1
    assert activity.replies_per_turn == (1,)
    assert activity.tool_calls == 1  # the call side only — result not doubled
    assert activity.tokens_in == 1500  # peak, not the 2500 sum
    assert activity.tokens_out == 1000  # sum of outputs


# ─── file-edit recognition (structured diff cards, #diff-viewer) ─────────────


def test_read_turns_renders_recognized_file_edit_as_structured_entry() -> None:
    events = {
        "status": "running",
        "title": "Rename a symbol",
        "events": [
            {
                "ts": "2026-07-08T10:00:00+00:00",
                "type": "user",
                "payload": {"text": "Rename foo to bar"},
            },
            {
                "ts": "2026-07-08T10:00:01+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "edit-1",
                    "operation": "Edit",
                    "tool_input": {"path": "a.py", "old_string": "foo", "new_string": "bar"},
                },
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    turns = adapter.read_turns(CWD, "sess-1")

    assert len(turns) == 1
    (entry,) = turns[0].entries
    assert entry.role == "file_edit"
    assert entry.file_edit == FileEdit(path="a.py", old_text="foo", new_text="bar")
    # The one-liner text still carries the label + path for a role-unaware reader.
    assert entry.text == "edit-1: Edit a.py"


def test_read_turns_renders_multiedit_batch_as_one_entry_per_edit() -> None:
    events = {
        "status": "running",
        "events": [
            {
                "ts": "2026-07-08T10:00:00+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "edit-2",
                    "operation": "MultiEdit",
                    "tool_input": {
                        "file_path": "b.py",
                        "edits": [
                            {"old_string": "a", "new_string": "b"},
                            {"old_string": "c", "new_string": "d"},
                        ],
                    },
                },
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    turns = adapter.read_turns(CWD, "sess-1")

    assert len(turns) == 1
    entries = turns[0].entries
    assert [e.role for e in entries] == ["file_edit", "file_edit"]
    assert [e.file_edit for e in entries] == [
        FileEdit(path="b.py", old_text="a", new_text="b"),
        FileEdit(path="b.py", old_text="c", new_text="d"),
    ]


def test_read_turns_non_file_edit_operation_stays_generic_tool() -> None:
    """Regression guard: an operation outside FILE_EDIT_TOOL_NAMES renders
    exactly as today's generic tool line, with no structured file_edit."""
    events = {
        "status": "running",
        "events": [
            {
                "ts": "2026-07-08T10:00:00+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "shell",
                    "operation": "Bash",
                    "tool_input": {"command": "ls"},
                },
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    turns = adapter.read_turns(CWD, "sess-1")

    (entry,) = turns[0].entries
    assert entry.role == "tool"
    assert entry.file_edit is None
    assert entry.text == "shell: Bash command='ls'"


def test_read_turns_file_edit_operation_with_string_input_falls_through() -> None:
    """A shell-style bare-string tool_input under an edit-shaped operation can't
    be normalized (from_tool_call returns ()), so it degrades to the generic
    tool line rather than raising — Mewbo's tool_input shape is polymorphic."""
    events = {
        "status": "running",
        "events": [
            {
                "ts": "2026-07-08T10:00:00+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "edit-3",
                    "operation": "str_replace",
                    "tool_input": "apply this patch somehow",
                },
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    turns = adapter.read_turns(CWD, "sess-1")

    (entry,) = turns[0].entries
    assert entry.role == "tool"
    assert entry.file_edit is None
    assert entry.text == "edit-3: str_replace apply this patch somehow"


def test_read_turns_renders_recognized_todo_write_as_structured_entry() -> None:
    """A Mewbo tool event whose ``operation`` is a todo writer renders one
    ``role="todo"`` entry carrying the normalized list — the todo sibling of the
    file-edit branch, gated on ``operation`` before the generic tool line."""
    events = {
        "status": "running",
        "events": [
            {
                "ts": "2026-07-08T10:00:00+00:00",
                "type": "tool",
                "payload": {
                    "tool_id": "plan-1",
                    "operation": "TodoWrite",
                    "tool_input": {
                        "todos": [
                            {"content": "Scope the change", "status": "completed"},
                            {"content": "Ship it", "status": "in_progress"},
                        ]
                    },
                },
            },
        ],
    }
    adapter = _adapter(_transport(events=events))
    turns = adapter.read_turns(CWD, "sess-1")

    (entry,) = turns[0].entries
    assert entry.role == "todo"
    assert entry.text == "1/2 done · Ship it"
    assert entry.todo is not None
    assert entry.todo.items == (
        TodoItem(content="Scope the change", status="completed"),
        TodoItem(content="Ship it", status="in_progress"),
    )


# ─── error paths: degrade, never raise (adapter); typed raise (client) ───────


def _raising_transport(exc: Exception) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def test_timeout_degrades_activity_to_unknown() -> None:
    adapter = _adapter(_raising_transport(httpx.ConnectTimeout("boom")))
    assert adapter.parse_activity(CWD, "sess-1").state is AgentActivityState.UNKNOWN
    assert adapter.read_turns(CWD, "sess-1") == ()
    assert adapter.list_sessions(CWD) == []


def test_http_401_degrades_activity_to_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401, "reason": "bad key"}})

    adapter = _adapter(httpx.MockTransport(handler))
    assert adapter.parse_activity(CWD, "sess-1").state is AgentActivityState.UNKNOWN


def test_malformed_json_degrades_activity_to_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>definitely not json</html>")

    adapter = _adapter(httpx.MockTransport(handler))
    assert adapter.parse_activity(CWD, "sess-1").state is AgentActivityState.UNKNOWN


def test_malformed_event_rows_degrade_fields_not_raise() -> None:
    events = {
        "status": "running",
        "events": [
            "not-a-dict",
            {"ts": 12345, "type": None, "payload": "nope"},
            {"ts": "2026-06-11T10:00:00+00:00", "type": "user", "payload": {"text": "hi"}},
        ],
    }
    adapter = _adapter(_transport(events=events))
    activity = adapter.parse_activity(CWD, "sess-1")
    assert activity.state is AgentActivityState.WORKING
    assert activity.human_turns == 1


def test_client_raises_typed_error_never_httpx() -> None:
    client = MewboClient(MewboConfig(), transport=_raising_transport(httpx.ConnectTimeout("t")))
    with pytest.raises(MewboError):
        client.create_session(cwd="/x")

    def unauthorized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401, "reason": "bad key"}})

    client = MewboClient(MewboConfig(), transport=httpx.MockTransport(unauthorized))
    with pytest.raises(MewboError, match="401"):
        client.create_session(cwd="/x")


# ─── client wire details ─────────────────────────────────────────────────────


def test_create_session_sends_cwd_api_key_and_best_effort_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROVE_TEST_MEWBO_KEY", "sekret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST" and request.url.path == "/api/sessions":
            return httpx.Response(200, json={"session_id": "remote-123"})
        # Title PATCH fails — must stay best-effort, the created id still returns.
        return httpx.Response(500, json={"error": {"code": 500, "reason": "title broke"}})

    cfg = MewboConfig(api_key_env="GROVE_TEST_MEWBO_KEY")
    client = MewboClient(cfg, transport=httpx.MockTransport(handler))
    session_id = client.create_session(cwd="/tmp/wt", title="my task")

    assert session_id == "remote-123"
    create, title = seen
    assert create.headers["X-API-KEY"] == "sekret"
    assert b'"cwd": "/tmp/wt"' in create.content or b'"cwd":"/tmp/wt"' in create.content
    assert title.method == "PATCH"
    assert title.url.path == "/api/sessions/remote-123/title"


def test_create_session_forwards_model_in_body_when_set() -> None:
    # A per-create model rides the create body (mewbo's only forward point).
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"session_id": "remote-9"})

    client = MewboClient(MewboConfig(), transport=httpx.MockTransport(handler))
    assert client.create_session(cwd="/tmp/wt", model="claude-opus-4-8") == "remote-9"
    body = seen[0].content
    assert b'"model": "claude-opus-4-8"' in body or b'"model":"claude-opus-4-8"' in body

    # Omitted when unset — no stray null model key on the wire.
    seen.clear()
    client.create_session(cwd="/tmp/wt")
    assert b'"model"' not in seen[0].content


def test_create_session_without_id_in_response_raises() -> None:
    client = MewboClient(MewboConfig(), transport=_transport(create={"unexpected": True}))
    with pytest.raises(MewboError, match="session_id"):
        client.create_session()


# ─── list_sessions cwd filtering ─────────────────────────────────────────────


def test_list_sessions_filters_to_matching_context_cwd() -> None:
    sessions = {
        "sessions": [
            {
                "session_id": "match-1",
                "title": "In this worktree",
                "status": "running",
                "created_at": "2026-06-11T08:00:00+00:00",
                "context": {"cwd": str(CWD), "branch": "grove/fix-flaky"},
            },
            {
                "session_id": "other-cwd",
                "title": "Different worktree",
                "status": "completed",
                "context": {"cwd": "/somewhere/else"},
            },
            {
                # No cwd in context: cannot honestly be claimed for this dir.
                "session_id": "no-cwd",
                "title": "Console session",
                "status": "completed",
                "context": {"project": "demo"},
            },
        ]
    }
    adapter = _adapter(_transport(sessions=sessions))
    rows = adapter.list_sessions(CWD)

    assert [row.session_id for row in rows] == ["match-1"]
    summary = rows[0]
    assert summary.adapter_kind == "mewbo"
    assert summary.transcript_path is None  # remote-backed: no local file, by design
    assert summary.cwd == str(CWD)
    assert summary.git_branch == "grove/fix-flaky"
    assert summary.activity.state is AgentActivityState.WORKING


# ─── discover_all (the host-wide catalog scan, epic: Session Catalog) ──────


def test_discover_all_is_the_same_listing_unfiltered_by_cwd() -> None:
    """``discover_all`` is ``list_sessions``'s host-wide sibling: the SAME
    ``GET /api/sessions`` rows, with the cwd-equality filter removed — so a
    session recorded under a DIFFERENT cwd than any one caller's still
    surfaces. A row with no context cwd stays excluded either way (honest
    filtering, not a gap)."""
    sessions = {
        "sessions": [
            {
                "session_id": "match-1",
                "title": "In this worktree",
                "status": "running",
                "created_at": "2026-06-11T08:00:00+00:00",
                "context": {"cwd": str(CWD), "branch": "grove/fix-flaky"},
            },
            {
                "session_id": "other-cwd",
                "title": "Different worktree",
                "status": "completed",
                "created_at": "2026-06-10T08:00:00+00:00",
                "context": {"cwd": "/somewhere/else", "branch": "main"},
            },
            {
                # No cwd in context: cannot honestly be placed on any project.
                "session_id": "no-cwd",
                "title": "Console session",
                "status": "completed",
                "context": {"project": "demo"},
            },
        ]
    }
    adapter = _adapter(_transport(sessions=sessions))
    refs = adapter.discover_all()

    assert {ref.session_id for ref in refs} == {"match-1", "other-cwd"}
    by_id = {ref.session_id: ref for ref in refs}
    assert by_id["match-1"].cwd == str(CWD)
    assert by_id["match-1"].git_branch == "grove/fix-flaky"
    assert by_id["match-1"].adapter_kind == "mewbo"
    assert by_id["match-1"].transcript_path is None
    assert by_id["other-cwd"].cwd == "/somewhere/else"
    assert by_id["other-cwd"].git_branch == "main"


def test_discover_all_empty_when_no_client_configured() -> None:
    adapter = MewboAdapter()
    assert adapter.discover_all() == ()
