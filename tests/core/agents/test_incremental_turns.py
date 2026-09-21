"""Deterministic processed-range coverage for folder-owned turn projection.

``read_messages`` folds incrementally; ``read_turns`` used to re-render every
historical turn on every append, because its outer ``ResultMemo`` keyed on a
stat signature that any byte of growth invalidates.  These tests measure the
message RANGE the turn renderer walks rather than wall time, so the guard fails
for a projection that rebuilds whole history even while its output stays
byte-identical.  The byte-identical half alone cannot see the defect; the range
half alone cannot see a wrong answer.  Both are required.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome, _TranscriptParser
from grove.core.agents.codex import CodexAdapter, _RolloutParser
from grove.core.agents.model import AgentMessage, ContentBlock, SessionTurn
from grove.core.agents.transcript_scope import config_dir_scope
from grove.core.agents.turn_projection import TurnProjection

_CODEX_CWD = Path("/work/project")
_CODEX_SID = "11111111-1111-7111-8111-111111111111"


# ─── harness ─────────────────────────────────────────────────────────────────


def _render_ranges(
    monkeypatch: pytest.MonkeyPatch, owner: type, name: str
) -> Callable[[], list[int]]:
    """Record the message-slice length handed to the turn renderer per call.

    The sum over one ``read_turns`` is the processed range: a frozen-prefix
    projection walks only the tail turn's messages, a whole-history rebuild
    walks every message that was ever folded.
    """
    original = getattr(owner, name).__func__
    ranges: list[int] = []

    def counted(cls: type, messages: Sequence[AgentMessage], **kwargs: Any) -> Any:
        ranges.append(len(messages))
        return original(cls, messages, **kwargs)

    monkeypatch.setattr(owner, name, classmethod(counted))
    return lambda: ranges


def test_the_open_turn_never_advances_the_frozen_carry() -> None:
    """Re-reading a live turn must not fold its records into the carry twice.

    The open span is re-rendered on every read, so it folds into a FORK; the
    persistent carry may only advance when a span is FREEZES.  Fold counts
    cannot see this — the tail is re-rendered either way — so the assertion is
    on the carry each frozen render is HANDED, which is the state a later turn
    resolves against.  ``TaskBoard``'s own operations happen to be idempotent,
    which hides the defect through the adapters; the carry contract is what the
    next fold type inherits, so it is pinned here directly.
    """
    carried: list[tuple[str, ...]] = []

    class _Carry:
        def __init__(self, seen: tuple[str, ...] = ()) -> None:
            self.seen = seen

    def render(messages: Sequence[AgentMessage], carry: _Carry) -> tuple[SessionTurn, ...]:
        carried.append(carry.seen)
        for message in messages:
            carry.seen = (*carry.seen, message.text())
        return tuple(
            SessionTurn(user_text=message.text()) for message in messages if message.role == "user"
        )

    projection: TurnProjection[_Carry] = TurnProjection(
        render=render,
        starts_turn=lambda message: message.role == "user",
        fork_carry=lambda carry: _Carry() if carry is None else _Carry(carry.seen),
    )

    def _prompt(text: str) -> AgentMessage:
        return AgentMessage(role="user", content=(ContentBlock(type="text", text=text),))

    first, second, third = _prompt("first"), _prompt("second"), _prompt("third")

    projection.turns((first,))
    projection.turns((first, second))
    # Read the live turn again before it closes: the re-render must not reach
    # the persistent carry, or the next freeze inherits "second" twice.
    projection.turns((first, second, AgentMessage(role="assistant", content=())))
    projection.turns((first, second, AgentMessage(role="assistant", content=()), third))

    # The final freeze folded "second" and the assistant record into the carry
    # exactly once, however many times the open turn was re-read.
    final_carry = carried[-1]
    assert final_carry == ("first", "second", ""), f"carry double-applied: {final_carry}"


# ─── Claude ──────────────────────────────────────────────────────────────────


def _claude_record(
    kind: str, uuid: str, timestamp: str, content: str | list[dict[str, Any]]
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "type": kind,
        "uuid": uuid,
        "timestamp": timestamp,
        "isSidechain": False,
    }
    if kind == "assistant":
        raw["message"] = {
            "id": f"message-{uuid}",
            "role": "assistant",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "content": content,
        }
    else:
        raw["message"] = {"role": "user", "content": content}
    return raw


def _claude_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ClaudeCodeAdapter, Path, Path, str]:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    adapter = ClaudeCodeAdapter()
    adapter.clear_caches()
    cwd = tmp_path / "work"
    session_id = "11111111-1111-4111-8111-111111111111"
    transcript = cfg / "projects" / _ClaudeHome.encode_cwd(cwd) / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    return adapter, cwd, transcript, session_id


def _claude_history(turns: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index in range(turns):
        stamp = f"2026-09-17T10:{index // 60:02}:{index % 60:02}Z"
        records.append(_claude_record("user", f"u{index}", stamp, f"ask {index}"))
        records.append(
            _claude_record(
                "assistant",
                f"a{index}",
                stamp,
                [{"type": "text", "text": f"reply {index}"}],
            )
        )
    return records


def _write(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _append(path: Path, *records: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.writelines(json.dumps(record) + "\n" for record in records)


def test_claude_append_renders_only_the_tail_turn_not_all_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One appended record must not re-render 80 historical messages.

    The frozen prefix is reused by reference, so the renderer is handed only the
    open turn's own messages.  Deleting that reuse leaves the output identical
    and makes this assertion fail — which is exactly why the range is measured
    beside the equality check below.
    """
    adapter, cwd, transcript, session_id = _claude_install(tmp_path, monkeypatch)
    history = _claude_history(40)
    _write(transcript, history)
    cold = adapter.read_turns(cwd, session_id)
    assert len(cold) == 40

    ranges = _render_ranges(monkeypatch, _TranscriptParser, "_turns_from_messages")
    _append(
        transcript,
        _claude_record(
            "assistant", "late", "2026-09-17T11:00:00Z", [{"type": "text", "text": "late"}]
        ),
    )
    warm = adapter.read_turns(cwd, session_id)

    assert len(warm) == 40
    assert warm[:-1] == cold[:-1]
    assert [entry.text for entry in warm[-1].entries] == ["reply 39", "late"]
    # 80 historical messages were folded; the tail turn owns 3 of them.
    assert sum(ranges()) <= 4, f"turn renderer walked {sum(ranges())} messages, expected the tail"


def test_claude_incremental_turns_match_a_full_parse_over_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-identical output against the unmemoized whole-history parser."""
    adapter, cwd, transcript, session_id = _claude_install(tmp_path, monkeypatch)
    _write(transcript, _claude_history(6))
    adapter.read_turns(cwd, session_id)

    _append(
        transcript,
        _claude_record("user", "u6", "2026-09-17T10:06:00Z", "ask 6"),
        _claude_record(
            "assistant", "a6", "2026-09-17T10:06:01Z", [{"type": "text", "text": "reply 6"}]
        ),
    )
    incremental = adapter.read_turns(cwd, session_id)
    expected = _TranscriptParser(adapter._read(adapter.locate_transcripts(cwd, session_id))).turns()

    assert incremental == expected


def test_claude_last_is_a_slice_over_the_complete_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``last`` never keys a cache — it windows one folder-owned tuple.

    Alternating windows over one parse must agree with each other, which a
    per-``last`` memo entry cannot guarantee once the file grows between them.
    """
    adapter, cwd, transcript, session_id = _claude_install(tmp_path, monkeypatch)
    _write(transcript, _claude_history(5))

    whole = adapter.read_turns(cwd, session_id)
    assert adapter.read_turns(cwd, session_id, last=2) == whole[-2:]
    assert adapter.read_turns(cwd, session_id, last=0) == ()
    assert adapter.read_turns(cwd, session_id, last=99) == whole


def test_claude_late_tool_result_rebuilds_the_turn_it_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A result landing turns later must resolve a call in a FROZEN turn.

    Outcomes are membership-tested, never truthiness-tested, so a result that
    carries no text still settles its call.  Without the retroactive path the
    first turn keeps rendering ``running`` forever.
    """
    adapter, cwd, transcript, session_id = _claude_install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _claude_record("user", "u0", "2026-09-17T10:00:00Z", "run it"),
            _claude_record(
                "assistant",
                "a0",
                "2026-09-17T10:00:01Z",
                [{"type": "tool_use", "id": "call-1", "name": "Bash", "input": {}}],
            ),
            _claude_record("user", "u1", "2026-09-17T10:00:02Z", "meanwhile"),
            _claude_record(
                "assistant", "a1", "2026-09-17T10:00:03Z", [{"type": "text", "text": "sure"}]
            ),
        ],
    )
    running = adapter.read_turns(cwd, session_id)
    assert running[0].entries[0].tool is not None
    assert running[0].entries[0].tool.status == "running"

    _append(
        transcript,
        {
            "type": "user",
            "uuid": "result-1",
            "timestamp": "2026-09-17T10:00:04Z",
            "isSidechain": False,
            "message": {
                "role": "user",
                # An EMPTY result still resolves the call: membership decides.
                "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": ""}],
            },
        },
    )
    settled = adapter.read_turns(cwd, session_id)
    expected = _TranscriptParser(adapter._read(adapter.locate_transcripts(cwd, session_id))).turns()

    assert settled[0].entries[0].tool is not None
    assert settled[0].entries[0].tool.status == "ok"
    assert settled == expected


def test_claude_late_task_update_keeps_one_board_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tail ``TaskUpdate`` resolves against a ``TaskCreate`` turns earlier.

    The board is one fold carried across the frozen boundary; restarting it at
    the tail would drop the create and render a bare tool entry instead.
    """
    adapter, cwd, transcript, session_id = _claude_install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _claude_record("user", "u0", "2026-09-17T10:00:00Z", "plan it"),
            _claude_record(
                "assistant",
                "a0",
                "2026-09-17T10:00:01Z",
                [
                    {
                        "type": "tool_use",
                        "id": "tc1",
                        "name": "TaskCreate",
                        "input": {"subject": "write the parser"},
                    }
                ],
            ),
            {
                "type": "user",
                "uuid": "tc1-result",
                "timestamp": "2026-09-17T10:00:02Z",
                "isSidechain": False,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tc1",
                            "content": "Task #7 created successfully: write the parser",
                        }
                    ],
                },
            },
            _claude_record("user", "u1", "2026-09-17T10:00:03Z", "carry on"),
            _claude_record(
                "assistant", "a1", "2026-09-17T10:00:04Z", [{"type": "text", "text": "working"}]
            ),
        ],
    )
    adapter.read_turns(cwd, session_id)

    _append(
        transcript,
        _claude_record(
            "assistant",
            "a2",
            "2026-09-17T10:00:05Z",
            [
                {
                    "type": "tool_use",
                    "id": "tu1",
                    "name": "TaskUpdate",
                    "input": {"taskId": "7", "status": "completed"},
                }
            ],
        ),
    )
    turns = adapter.read_turns(cwd, session_id)
    expected = _TranscriptParser(adapter._read(adapter.locate_transcripts(cwd, session_id))).turns()

    todo = turns[-1].entries[-1].todo
    assert todo is not None
    assert [(item.content, item.status) for item in todo.items] == [
        ("write the parser", "completed")
    ]
    assert turns == expected


def test_claude_split_block_continuation_rewrites_a_frozen_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A continuation REPLACES a folded record, possibly inside a FROZEN turn.

    The published prefix is therefore validated by identity rather than trusted:
    the replacement is a different object at the same position, and a projection
    that reused its frozen turns would keep serving the pre-merge text forever.
    The continuation must land in an EARLIER turn than the open one, or nothing
    is frozen and the guard is never exercised.
    """
    adapter, cwd, transcript, session_id = _claude_install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _claude_record("user", "u0", "2026-09-17T10:00:00Z", "go"),
            {
                "type": "assistant",
                "uuid": "a0",
                "timestamp": "2026-09-17T10:00:01Z",
                "isSidechain": False,
                "message": {
                    "id": "split",
                    "role": "assistant",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "before"}],
                },
            },
            _claude_record("user", "u1", "2026-09-17T10:00:02Z", "next"),
            _claude_record(
                "assistant", "a2", "2026-09-17T10:00:03Z", [{"type": "text", "text": "second"}]
            ),
        ],
    )
    first = adapter.read_turns(cwd, session_id)
    assert [entry.text for entry in first[0].entries] == ["before"]

    _append(
        transcript,
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-09-17T10:00:01.100Z",
            "isSidechain": False,
            "message": {
                "id": "split",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "after"}],
            },
        },
    )
    turns = adapter.read_turns(cwd, session_id)
    expected = _TranscriptParser(adapter._read(adapter.locate_transcripts(cwd, session_id))).turns()

    # The merged message's two text blocks render as two entries — in the turn
    # that had already been frozen by the time the continuation arrived.
    assert [entry.text for entry in turns[0].entries] == ["before", "after"]
    assert turns == expected


# ─── Codex ───────────────────────────────────────────────────────────────────


def _codex_record(record_type: str, payload: dict[str, object], second: int) -> dict[str, object]:
    return {
        "timestamp": f"2026-09-17T10:00:{second:02d}.000Z",
        "type": record_type,
        "payload": payload,
    }


def _codex_meta() -> dict[str, object]:
    return _codex_record(
        "session_meta", {"id": _CODEX_SID, "cwd": str(_CODEX_CWD), "model": "gpt-test"}, 0
    )


def _codex_user(text: str, second: int) -> dict[str, object]:
    return _codex_record(
        "response_item",
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
        second,
    )


def _codex_assistant(text: str, second: int) -> dict[str, object]:
    return _codex_record(
        "response_item",
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        },
        second,
    )


def _codex_path(home: Path) -> Path:
    path = home / "sessions" / "2026" / "09" / "17" / f"rollout-2026-09-17-{_CODEX_SID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_codex_append_renders_only_the_tail_turn_not_all_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Codex rollout has the identical shape and the identical defect."""
    home = tmp_path / "codex"
    rollout = _codex_path(home)
    history: list[dict[str, object]] = [_codex_meta()]
    for index in range(20):
        history.append(_codex_user(f"ask {index}", index))
        history.append(_codex_assistant(f"reply {index}", index))
    _write(rollout, history)  # type: ignore[arg-type]
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(home)):
        cold = adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        assert len(cold) == 20

        ranges = _render_ranges(monkeypatch, _RolloutParser, "_turns_from_messages")
        _append(rollout, _codex_assistant("late", 21))  # type: ignore[arg-type]
        warm = adapter.read_turns(_CODEX_CWD, _CODEX_SID)

    assert len(warm) == 20
    assert warm[:-1] == cold[:-1]
    assert sum(ranges()) <= 4, f"turn renderer walked {sum(ranges())} messages, expected the tail"


def test_codex_incremental_turns_match_a_full_parse_over_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Byte-identical output against the whole-history rollout parser."""
    del monkeypatch
    home = tmp_path / "codex"
    rollout = _codex_path(home)
    _write(  # type: ignore[arg-type]
        rollout,
        [_codex_meta(), _codex_user("first", 1), _codex_assistant("first reply", 2)],
    )
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(home)):
        adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        _append(  # type: ignore[arg-type]
            rollout, _codex_user("second", 3), _codex_assistant("second reply", 4)
        )
        incremental = adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        expected = _RolloutParser(
            adapter._read(adapter.locate_transcripts(_CODEX_CWD, _CODEX_SID))
        ).turns()

    assert incremental == expected


def test_codex_late_function_call_output_resolves_a_frozen_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An output arriving a turn later settles its call, empty body included."""
    del monkeypatch
    home = tmp_path / "codex"
    rollout = _codex_path(home)
    _write(  # type: ignore[arg-type]
        rollout,
        [
            _codex_meta(),
            _codex_user("run it", 1),
            _codex_record(
                "response_item",
                {"type": "function_call", "name": "shell", "arguments": "{}", "call_id": "c1"},
                2,
            ),
            _codex_user("meanwhile", 3),
            _codex_assistant("sure", 4),
        ],
    )
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(home)):
        running = adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        assert running[0].entries[0].tool is not None
        assert running[0].entries[0].tool.status == "running"

        _append(  # type: ignore[arg-type]
            rollout,
            _codex_record(
                "response_item", {"type": "function_call_output", "call_id": "c1", "output": ""}, 5
            ),
        )
        settled = adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        expected = _RolloutParser(
            adapter._read(adapter.locate_transcripts(_CODEX_CWD, _CODEX_SID))
        ).turns()

    assert settled[0].entries[0].tool is not None
    assert settled[0].entries[0].tool.status == "ok"
    assert settled == expected


def test_codex_out_of_order_record_rebuilds_to_the_full_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An out-of-order line resorts the fold; a broader rebuild is acceptable."""
    del monkeypatch
    home = tmp_path / "codex"
    rollout = _codex_path(home)
    _write(  # type: ignore[arg-type]
        rollout,
        [_codex_meta(), _codex_user("first", 4), _codex_assistant("first reply", 5)],
    )
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(home)):
        adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        _append(rollout, _codex_user("earlier", 1))  # type: ignore[arg-type]
        turns = adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        expected = _RolloutParser(
            adapter._read(adapter.locate_transcripts(_CODEX_CWD, _CODEX_SID))
        ).turns()

    assert [turn.user_text for turn in turns] == ["earlier", "first"]
    assert turns == expected


def test_codex_last_is_a_slice_over_the_complete_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del monkeypatch
    home = tmp_path / "codex"
    rollout = _codex_path(home)
    history: list[dict[str, object]] = [_codex_meta()]
    for index in range(4):
        history.append(_codex_user(f"ask {index}", index))
        history.append(_codex_assistant(f"reply {index}", index))
    _write(rollout, history)  # type: ignore[arg-type]
    adapter = CodexAdapter()

    with config_dir_scope("CODEX_HOME", str(home)):
        whole = adapter.read_turns(_CODEX_CWD, _CODEX_SID)
        assert adapter.read_turns(_CODEX_CWD, _CODEX_SID, last=2) == whole[-2:]
        assert adapter.read_turns(_CODEX_CWD, _CODEX_SID, last=0) == ()
