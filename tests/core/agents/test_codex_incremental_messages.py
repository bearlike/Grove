"""Regression coverage for Codex's retained incremental message projection.

Codex appends a rollout one record at a time.  ``TranscriptCache`` already avoids
re-reading old JSONL bytes, but a fresh ``_RolloutParser.messages()`` used to
normalise every retained line again on every append.  These tests measure
normalisation calls rather than wall time and pin the harder semantic boundary:
a response stays open across fragments and receives its usage only when the
later ``token_count.last_token_usage`` record arrives.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from grove.core.agents.codex import (
    CodexAdapter,
    _ActivityProjector,
    _EventState,
    _LineFolder,
    _MessageProjector,
    _RolloutLine,
    _RolloutParser,
)
from grove.core.agents.transcript_scope import config_dir_scope

_CWD = Path("/work/project")
_SID = "11111111-1111-7111-8111-111111111111"


def _record(record_type: str, payload: dict[str, object], second: int) -> dict[str, object]:
    return {
        "timestamp": f"2026-09-16T10:00:{second:02d}.000Z",
        "type": record_type,
        "payload": payload,
    }


def _meta(sid: str = _SID) -> dict[str, object]:
    return _record("session_meta", {"id": sid, "cwd": str(_CWD), "model": "gpt-test"}, 0)


def _user(text: str, second: int) -> dict[str, object]:
    return _record(
        "response_item",
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        second,
    )


def _assistant(text: str, second: int) -> dict[str, object]:
    return _record(
        "response_item",
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
        second,
    )


def _usage(second: int, output: int) -> dict[str, object]:
    return _record(
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "last_token_usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": output,
                }
            },
        },
        second,
    )


def _path(home: Path, sid: str = _SID) -> Path:
    path = home / "sessions" / "2026" / "09" / "16" / f"rollout-2026-09-16-{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _append(path: Path, record: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")


def _full_messages(path: Path) -> tuple[object, ...]:
    lines = [
        _RolloutLine(raw=json.loads(raw), index=index)
        for index, raw in enumerate(path.read_text(encoding="utf-8").splitlines())
    ]
    lines.sort(key=lambda line: line.sort_key)
    return _RolloutParser(lines).messages()


def _former_messages(lines: list[_RolloutLine]) -> tuple[object, ...]:  # noqa: PLR0912
    """The pre-incremental parser, retained here as an independent oracle."""
    exec_metrics: dict[str, tuple[int | None, int]] = {}
    for line in lines:
        if entry := line.exec_command_end_metrics():
            call_id, duration_ms, exit_code = entry
            exec_metrics[call_id] = (duration_ms, exit_code)

    out: list[object] = []
    pending: int | None = None
    previous_was_assistant = False
    model: str | None = None
    saw_turn_context = False
    for line in lines:
        if line.has_turn_usage_record:
            if pending is not None:
                if usage := line.turn_usage():
                    out[pending] = replace(out[pending], usage=usage)
                pending = None
            previous_was_assistant = False
            continue
        if turn_model := line.turn_context_model():
            pending = None
            previous_was_assistant = False
            model = turn_model
            saw_turn_context = True
            continue
        if not saw_turn_context and line.record_type == "session_meta":
            payload = line.raw.get("payload")
            candidate = payload.get("model") if isinstance(payload, dict) else None
            if isinstance(candidate, str) and candidate:
                model = candidate
        message = line.to_message(exec_metrics)
        if message is None:
            continue
        if message.role == "assistant":
            if pending is not None and previous_was_assistant:
                prior = out[pending]
                out[pending] = replace(prior, content=prior.content + message.content)
            else:
                out.append(replace(message, model=model))
                pending = len(out) - 1
            previous_was_assistant = True
            continue
        if message.role == "user":
            pending = None
        previous_was_assistant = False
        out.append(message)
    return tuple(out)


def _normalization_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[], int]:
    """Count actual line-to-message normalisation calls, not elapsed time."""
    original = _RolloutLine.to_message
    calls = 0

    def counted(self: _RolloutLine, *args: Any, **kwargs: Any) -> object:
        nonlocal calls
        calls += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(_RolloutLine, "to_message", counted)
    return lambda: calls


def _former_activity(lines: list[_RolloutLine]) -> object:
    """The pre-incremental activity loop, separately retained as the oracle."""
    buckets: list[int] = []
    tool_calls = 0
    last_event_at = None
    events = _EventState()
    tail: _RolloutLine | None = None
    open_questions: dict[str, object] = {}
    for line in lines:
        timestamp = line.timestamp
        if timestamp is not None and (last_event_at is None or timestamp > last_event_at):
            last_event_at = timestamp
        if events.consume(line):
            continue
        if line.is_human_turn:
            buckets.append(0)
            tail = line
        elif line.is_assistant:
            if buckets:
                buckets[-1] += 1
            tail = line
        elif line.is_tool_call:
            tool_calls += 1
            tail = line
            _RolloutParser._track_question(line, open_questions)  # type: ignore[arg-type]
        elif line.is_tool_call_output and line.call_id:
            open_questions.pop(line.call_id, None)
    raw_task = next((line.message_text() for line in lines if line.is_human_turn), None)
    current_task = raw_task if raw_task is None else raw_task[:500]
    return {
        "state": events.state(tail),
        "questions": tuple(question for group in open_questions.values() for question in group),
        "current_task": current_task,
        "human_turns": len(buckets),
        "assistant_replies": sum(buckets),
        "replies_per_turn": tuple(buckets),
        "tool_calls": tool_calls,
        "tokens_in": events.tokens_in,
        "tokens_out": events.tokens_out,
        "context": events.context,
        "last_event_at": last_event_at,
    }


def test_activity_projector_consumes_each_record_once_and_matches_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Activity's raw fold advances by delta without re-reading retained lines."""
    records = [_meta(), _user("first", 1), _assistant("reply", 2), _usage(3, 4)]
    lines = [_RolloutLine(raw=record, index=index) for index, record in enumerate(records)]
    projector = _ActivityProjector()
    original = _EventState.consume
    consumes = 0

    def counted(self: _EventState, line: _RolloutLine) -> bool:
        nonlocal consumes
        consumes += 1
        return original(self, line)

    monkeypatch.setattr(_EventState, "consume", counted)
    for line in lines:
        projector.consume(line)
    actual = projector.activity()
    consumed_incrementally = consumes
    expected = _former_activity(lines)

    assert consumed_incrementally == len(lines)
    for field, value in expected.items():
        assert getattr(actual, field) == value


def test_incremental_projector_matches_the_pre_incremental_oracle() -> None:
    """Model fallback, fragments, results, and delayed usage keep old semantics."""
    records = [
        _meta(),
        _assistant("before context", 1),
        _record("turn_context", {"model": "gpt-after-context"}, 2),
        _user("prompt", 3),
        _assistant("reply", 4),
        _record(
            "response_item",
            {"type": "function_call", "name": "shell", "arguments": "{}", "call_id": "c1"},
            5,
        ),
        _record(
            "event_msg",
            {
                "type": "exec_command_end",
                "call_id": "c1",
                "duration": {"secs": 0, "nanos": 500_000_000},
                "exit_code": 0,
            },
            6,
        ),
        _record(
            "response_item",
            {"type": "function_call_output", "call_id": "c1", "output": "ok"},
            7,
        ),
        _usage(8, 9),
    ]
    lines = [_RolloutLine(raw=record, index=index) for index, record in enumerate(records)]
    folder = _LineFolder()
    for record in records:
        folder.add(record, "source")

    assert folder.messages() == _former_messages(lines)


def test_projector_publishes_one_snapshot_for_a_cold_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1,000-line cold rollout makes one output tuple, not 1,000 prefixes."""
    original = _MessageProjector._publish
    publications = 0

    def counted(self: _MessageProjector) -> None:
        nonlocal publications
        publications += 1
        original(self)

    monkeypatch.setattr(_MessageProjector, "_publish", counted)
    folder = _LineFolder()
    for index in range(1_000):
        # Matching timestamps retain physical order through the parser's index
        # tie-break without exercising the separate out-of-order rebuild arm.
        folder.add(_assistant(f"fragment {index}", 1), "source")
    snapshot = folder.messages()

    assert len(snapshot) == 1
    assert publications == 1
    assert folder.messages() is snapshot


def test_line_folder_records_remains_constant_cost_as_history_grows() -> None:
    """The cache probes records twice per ingested JSON line.

    Returning a copied tuple here turns the cold ingest into quadratic pointer
    copying before message normalisation even starts.  ``records`` must hand the
    folder's raw list to the cache; its private cache lock provides the safety.
    """
    folder = _LineFolder()
    sizes: list[int] = []
    raw = _assistant("fragment", 1)
    for _ in range(64):
        folder.add(raw, "source")
        records = folder.records()
        assert records is folder.records()
        sizes.append(len(records))

    assert sizes == list(range(1, 65))


def test_appends_normalize_only_the_open_response_and_retain_its_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A content record and its delayed usage update only their response group.

    The cached result may copy its outer tuple, but completed messages are
    immutable and must retain identity.  A line-to-message call counter makes
    the performance contract independently observable: adding a response
    fragment costs one normalisation and adding its token claim costs none.
    """
    home = tmp_path / "codex"
    rollout = _path(home)
    _write(
        rollout,
        [
            _meta(),
            _user("first", 1),
            _assistant("first reply", 2),
            _usage(3, 1),
            _user("second", 4),
        ],
    )
    adapter = CodexAdapter()
    calls = _normalization_counter(monkeypatch)

    with config_dir_scope("CODEX_HOME", str(home)):
        before = adapter.read_messages(_CWD, _SID)
        baseline_calls = calls()
        _append(rollout, _assistant("second reply", 5))
        after_content = adapter.read_messages(_CWD, _SID)

        calls_after_content = calls()
        _append(rollout, _usage(6, 7))
        after_usage = adapter.read_messages(_CWD, _SID)

    # Initial history takes its normal parse.  Each incremental operation then
    # touches only its own new record (the usage row is a structural boundary,
    # not a second conversation message).
    assert calls_after_content - baseline_calls == 1
    assert calls() == calls_after_content
    assert after_content is not before
    assert after_content[:3] == before
    assert all(new is old for new, old in zip(after_content[:3], before[:3], strict=True))
    assert after_usage[:3] == before
    assert all(new is old for new, old in zip(after_usage[:3], before[:3], strict=True))
    assert after_usage[-1] is not after_content[-1]
    assert after_usage[-1].usage is not None
    assert after_usage[-1].usage.output == 7
    assert after_usage == _full_messages(rollout)


def test_dual_records_and_delayed_usage_keep_one_response_equivalent_to_full_parse(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An event mirror is not a second response and a late claim applies once."""
    home = tmp_path / "codex"
    rollout = _path(home)
    _write(rollout, [_meta(), _user("work", 1)])
    adapter = CodexAdapter()
    calls = _normalization_counter(monkeypatch)

    with config_dir_scope("CODEX_HOME", str(home)):
        adapter.read_messages(_CWD, _SID)
        baseline_calls = calls()
        _append(rollout, _assistant("first fragment", 2))
        _append(
            rollout,
            _record("event_msg", {"type": "agent_message", "message": "first fragment"}, 3),
        )
        response = adapter.read_messages(_CWD, _SID)
        assert [message.role for message in response] == ["user", "assistant"]
        assert calls() - baseline_calls == 2  # exactly the two appended records, never history

        _append(rollout, _usage(4, 11))
        settled = adapter.read_messages(_CWD, _SID)

    assert len(settled) == 2
    assert settled[1].usage is not None
    assert settled[1].usage.output == 11
    assert settled == _full_messages(rollout)


def test_rotated_source_rebuilds_a_new_folder_without_old_messages(tmp_path: Path) -> None:
    """An atomic replacement gets a new inode, so no prior fold can survive."""
    home = tmp_path / "codex"
    rollout = _path(home)
    _write(rollout, [_meta(), _user("old", 1), _assistant("old reply", 2), _usage(3, 1)])
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(home)):
        old = adapter.read_messages(_CWD, _SID)
        replacement = rollout.with_suffix(".replacement")
        _write(replacement, [_meta(), _user("replacement", 1)])
        os.replace(replacement, rollout)
        rewritten = adapter.read_messages(_CWD, _SID)

    assert [message.text() for message in rewritten] == ["replacement"]
    assert rewritten[0] is not old[0]
    assert rewritten == _full_messages(rollout)


def test_profile_scopes_with_equal_session_ids_retain_separate_folds(tmp_path: Path) -> None:
    """A profile root is part of the source identity, never an ambient global."""
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    first = _path(first_home)
    second = _path(second_home)
    _write(
        first,
        [_meta(), _user("first profile", 1), _assistant("first reply", 2), _usage(3, 1)],
    )
    _write(
        second,
        [_meta(), _user("second profile", 1), _assistant("second reply", 2), _usage(3, 2)],
    )
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(first_home)):
        first_messages = adapter.read_messages(_CWD, _SID)
    with config_dir_scope("CODEX_HOME", str(second_home)):
        second_messages = adapter.read_messages(_CWD, _SID)

    _append(first, _user("follow up", 4))
    with config_dir_scope("CODEX_HOME", str(first_home)):
        first_after_append = adapter.read_messages(_CWD, _SID)
    with config_dir_scope("CODEX_HOME", str(second_home)):
        second_after_append = adapter.read_messages(_CWD, _SID)

    assert [message.text() for message in first_messages] == ["first profile", "first reply"]
    assert [message.text() for message in second_messages] == ["second profile", "second reply"]
    assert [message.text() for message in first_after_append] == [
        "first profile",
        "first reply",
        "follow up",
    ]
    assert second_after_append == second_messages
    assert all(new is old for new, old in zip(second_after_append, second_messages, strict=True))
