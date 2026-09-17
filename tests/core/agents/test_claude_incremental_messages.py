"""Regression coverage for incremental Claude message normalization.

The transcript cache already avoids re-parsing history.  These tests pin the
next boundary: normalized immutable messages are also preserved by identity
until the one source record that produced them changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grove.core.agents.claude_code import (
    ClaudeCodeAdapter,
    _ClaudeHome,
    _Record,
    _TranscriptParser,
)


def _record(
    kind: str,
    uuid: str,
    timestamp: str,
    content: str | list[dict[str, Any]],
    *,
    message_id: str | None = None,
    compact_summary: bool = False,
    parent_uuid: str | None = None,
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "type": kind,
        "uuid": uuid,
        "timestamp": timestamp,
        "isSidechain": False,
    }
    if kind == "assistant":
        raw["message"] = {
            "id": message_id or f"message-{uuid}",
            "role": "assistant",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "content": content,
        }
    elif kind == "user":
        raw["message"] = {"role": "user", "content": content}
        if compact_summary:
            raw["isCompactSummary"] = True
        if parent_uuid is not None:
            raw["parentUuid"] = parent_uuid
    return raw


def _boundary(uuid: str, timestamp: str) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "uuid": uuid,
        "timestamp": timestamp,
        "isSidechain": False,
        "compactMetadata": {"trigger": "manual", "cumulativeDroppedTokens": 100},
    }


def _install(
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


def _write(transcript: Path, records: list[dict[str, Any]]) -> None:
    transcript.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


def _append(transcript: Path, *records: dict[str, Any]) -> None:
    with transcript.open("a", encoding="utf-8") as file:
        file.writelines(json.dumps(record) + "\n" for record in records)


def test_append_reuses_the_normalized_prefix_and_converts_only_the_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "first"),
            _record(
                "assistant",
                "a1",
                "2026-09-16T10:00:01Z",
                [{"type": "text", "text": "one"}],
            ),
            _record("user", "u2", "2026-09-16T10:00:02Z", "second"),
            _record(
                "assistant",
                "a2",
                "2026-09-16T10:00:03Z",
                [{"type": "text", "text": "two"}],
            ),
        ],
    )

    conversions = 0
    original = _Record.to_message

    def counted(self: _Record, compaction: Any = None) -> Any:
        nonlocal conversions
        conversions += 1
        return original(self, compaction)

    monkeypatch.setattr(_Record, "to_message", counted)
    first = adapter.read_messages(cwd, session_id)
    before_append = conversions
    _append(
        transcript,
        _record("user", "u3", "2026-09-16T10:00:04Z", "third"),
        _record(
            "assistant",
            "a3",
            "2026-09-16T10:00:05Z",
            [{"type": "text", "text": "three"}],
        ),
    )
    second = adapter.read_messages(cwd, session_id)

    assert conversions - before_append == 2
    assert second[: len(first)] == first
    assert all(after is before for before, after in zip(first, second, strict=False))
    assert [message.text() for message in second] == [
        "first",
        "one",
        "second",
        "two",
        "third",
        "three",
    ]


def test_split_block_append_rebuilds_only_its_mutated_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "go"),
            _record(
                "assistant",
                "a1",
                "2026-09-16T10:00:01Z",
                [{"type": "text", "text": "before"}],
                message_id="split-message",
            ),
        ],
    )
    first = adapter.read_messages(cwd, session_id)

    _append(
        transcript,
        _record(
            "assistant",
            "a2",
            "2026-09-16T10:00:01.100Z",
            [{"type": "text", "text": "after"}],
            message_id="split-message",
        ),
    )
    second = adapter.read_messages(cwd, session_id)

    assert second[0] is first[0]
    assert second[1] is not first[1]
    assert second[1].text() == "before\nafter"


def test_late_compaction_summary_rebuilds_its_boundary_without_rebuilding_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "go"),
            _boundary("boundary", "2026-09-16T10:05:01Z"),
        ],
    )
    first = adapter.read_messages(cwd, session_id)
    first_boundary = next(message for message in first if message.role == "compaction")
    assert first_boundary.compaction is not None
    assert first_boundary.compaction.summary == ""

    # File order is boundary then summary, while the summary's provider clock is
    # earlier.  Parent UUID, not time order, owns this join.
    _append(
        transcript,
        _record(
            "user",
            "summary",
            "2026-09-16T10:05:00Z",
            "summary written later",
            compact_summary=True,
            parent_uuid="boundary",
        ),
    )
    second = adapter.read_messages(cwd, session_id)
    second_boundary = next(message for message in second if message.role == "compaction")

    assert second[0] is first[0]
    assert second_boundary is not first_boundary
    assert second_boundary.compaction is not None
    assert second_boundary.compaction.summary == "summary written later"


def test_rewrite_discards_normalized_messages_from_the_discarded_fold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "old"),
            _record(
                "assistant",
                "a1",
                "2026-09-16T10:00:01Z",
                [{"type": "text", "text": "discard me"}],
            ),
        ],
    )
    first = adapter.read_messages(cwd, session_id)

    _write(transcript, [_record("user", "replacement", "2026-09-16T10:01:00Z", "replacement")])
    second = adapter.read_messages(cwd, session_id)

    assert [message.text() for message in second] == ["replacement"]
    assert second[0] is not first[0]


def test_incremental_queue_delivery_keeps_the_reply_with_the_previous_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    queued = {
        "type": "attachment",
        "uuid": "delivery",
        "timestamp": "2026-09-16T10:00:01Z",
        "attachment": {
            "type": "queued_command",
            "prompt": "second prompt",
            "commandMode": "prompt",
            "origin": {"kind": "human"},
        },
    }
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "first prompt"),
            {
                "type": "queue-operation",
                "uuid": "enqueue",
                "timestamp": "2026-09-16T10:00:01Z",
                "operation": "enqueue",
                "content": "second prompt",
            },
            _record(
                "assistant",
                "a1",
                "2026-09-16T10:00:30Z",
                [{"type": "text", "text": "first answer"}],
            ),
        ],
    )
    adapter.read_messages(cwd, session_id)

    _append(
        transcript,
        {
            "type": "queue-operation",
            "uuid": "remove",
            "timestamp": "2026-09-16T10:00:42Z",
            "operation": "remove",
            "content": "second prompt",
        },
        queued,
        _record(
            "assistant",
            "a2",
            "2026-09-16T10:01:00Z",
            [{"type": "text", "text": "second answer"}],
        ),
    )

    turns = adapter.read_turns(cwd, session_id)
    assert [turn.user_text for turn in turns] == ["first prompt", "second prompt"]
    assert [[entry.text for entry in turn.entries] for turn in turns] == [
        ["first answer"],
        ["second answer"],
    ]
    assert adapter.pending_queue(cwd, session_id) == ()


def test_incremental_activity_matches_the_full_record_fold_after_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    records = [
        _record("user", "u1", "2026-09-16T10:00:00Z", "first"),
        _record(
            "assistant",
            "a1",
            "2026-09-16T10:00:01Z",
            [{"type": "text", "text": "first reply"}],
        ),
        _record("user", "u2", "2026-09-16T10:00:02Z", "second"),
        _record(
            "assistant",
            "a2",
            "2026-09-16T10:00:03Z",
            [{"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}}],
        ),
    ]
    _write(transcript, records)
    first = adapter.parse_activity(cwd, session_id)
    assert (
        first
        == _TranscriptParser(adapter._read(adapter.locate_transcripts(cwd, session_id))).activity()
    )

    append = [
        {
            "type": "user",
            "uuid": "result-1",
            "timestamp": "2026-09-16T10:00:04Z",
            "isSidechain": False,
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tool-1", "content": "ok"}],
            },
        },
        _record(
            "assistant",
            "a3",
            "2026-09-16T10:00:05Z",
            [{"type": "text", "text": "finished"}],
        ),
    ]
    _append(transcript, *append)

    incremental = adapter.parse_activity(cwd, session_id)
    expected = _TranscriptParser(
        adapter._read(adapter.locate_transcripts(cwd, session_id))
    ).activity()
    assert incremental == expected
    assert incremental.replies_per_turn == (1, 2)
    assert incremental.state.value == "waiting"


def test_incremental_activity_visits_only_the_appended_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    history = [
        _record("user", f"u{index}", f"2026-09-16T10:{index // 60:02}:{index % 60:02}Z", "ask")
        for index in range(180)
    ]
    _write(transcript, history)
    adapter.parse_activity(cwd, session_id)

    calls = 0
    original = _Record.is_human_turn.fget
    assert original is not None

    def counted(self: _Record) -> bool:
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(_Record, "is_human_turn", property(counted))
    _append(transcript, _record("assistant", "a1", "2026-09-16T11:00:00Z", "done"))
    activity = adapter.parse_activity(cwd, session_id)

    assert activity.human_turns == 180
    # The preceding 180 records were already folded. A whole-history activity
    # scan would invoke this predicate over all of them again.
    assert calls < 10


def test_incremental_activity_replays_a_late_timestamp_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "first"),
            _record("user", "u2", "2026-09-16T10:00:02Z", "second"),
        ],
    )
    adapter.parse_activity(cwd, session_id)
    _append(transcript, _record("assistant", "a1", "2026-09-16T10:00:01Z", "reply"))

    incremental = adapter.parse_activity(cwd, session_id)
    expected = _TranscriptParser(
        adapter._read(adapter.locate_transcripts(cwd, session_id))
    ).activity()
    assert incremental == expected
    assert incremental.replies_per_turn == (1, 0)


def test_incremental_activity_replays_a_changed_split_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    initial = _record(
        "assistant",
        "a1",
        "2026-09-16T10:00:01Z",
        [{"type": "text", "text": "before"}],
        message_id="split",
    )
    _write(transcript, [_record("user", "u1", "2026-09-16T10:00:00Z", "go"), initial])
    adapter.parse_activity(cwd, session_id)

    _append(
        transcript,
        _record(
            "assistant",
            "a2",
            "2026-09-16T10:00:01.100Z",
            [{"type": "tool_use", "id": "tool-1", "name": "Bash", "input": {}}],
            message_id="split",
        ),
    )

    incremental = adapter.parse_activity(cwd, session_id)
    expected = _TranscriptParser(
        adapter._read(adapter.locate_transcripts(cwd, session_id))
    ).activity()
    assert incremental == expected
    assert incremental.assistant_replies == 1
    assert incremental.tool_calls == 1


def test_tool_use_result_prune_is_lossless_to_every_public_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained ``toolUseResult`` shrinks to its 3 consumed keys, and every
    public reader (turns/digest/activity) sees the identical answer either way.

    A real ``toolUseResult`` can carry the tool's whole raw output (file
    contents, command stdout) alongside the handful of fields Grove's own
    fleet-tracking logic reads. This pins the equivalence against a FAT payload
    representative of that shape, not a hand-trimmed fixture that would pass
    trivially.
    """
    adapter, cwd, transcript, session_id = _install(tmp_path, monkeypatch)
    fat_output = "x" * 50_000
    ack = {
        "type": "user",
        "uuid": "ack",
        "timestamp": "2026-09-16T10:00:02Z",
        "isSidechain": False,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu1",
                    "content": "Async agent launched",
                }
            ],
        },
        # Fields nothing reads, sized to dwarf the 3 fields that matter — a
        # real Bash/Read tool_result can carry exactly this shape.
        "toolUseResult": {
            "status": "teammate_spawned",
            "name": "seam-mapper",
            "teammate_id": "seam-mapper@session-1",
            "agent_id": "seam-mapper@session-1",
            "stdout": fat_output,
            "filePath": "/some/large/file.txt",
            "content": fat_output,
        },
    }
    _write(
        transcript,
        [
            _record("user", "u1", "2026-09-16T10:00:00Z", "fan out"),
            {
                "type": "assistant",
                "uuid": "a1",
                "requestId": "r1",
                "isSidechain": False,
                "timestamp": "2026-09-16T10:00:01Z",
                "message": {
                    "id": "m1",
                    "role": "assistant",
                    "stop_reason": "tool_use",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu1",
                            "name": "Agent",
                            "input": {
                                "description": "map",
                                "subagent_type": "Explore",
                                "name": "seam-mapper",
                                "prompt": "go",
                            },
                        }
                    ],
                },
            },
            ack,
        ],
    )

    activity = adapter.parse_activity(cwd, session_id)
    # The launch ack must not close the spawn — the exact fact `.status`/`.name`
    # exist to decide, so the prune left the decision-bearing fields intact.
    assert activity.active_subagents == 1

    records = adapter._read(adapter.locate_transcripts(cwd, session_id))
    ack_record = next(rec for rec in records if rec.uuid == "ack")
    pruned = ack_record.tool_use_result
    assert pruned == {"status": "teammate_spawned", "name": "seam-mapper"}
    # The retained dict is genuinely smaller — not merely re-shaped.
    assert "stdout" not in ack_record.raw["toolUseResult"]
    assert "content" not in ack_record.raw["toolUseResult"]
    assert len(json.dumps(ack_record.raw["toolUseResult"])) < len(fat_output)
