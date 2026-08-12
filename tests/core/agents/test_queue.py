"""The harness queue: a delivered queued message becomes a turn, and what is
still waiting is READ from the harness rather than remembered by Grove.

Two provider shapes, both pinned against records copied from real on-host
stores rather than invented:

* Claude Code 2.1.x writes a queued message as an ``attachment`` carrying
  ``{type:"queued_command", prompt, commandMode, origin:{kind}, timestamp}`` —
  NOT a ``type:"user"`` line — plus ``queue-operation`` records for each
  enqueue / remove / dequeue / popAll. Measured over the 25 busiest sessions on
  the reference host: 729 human queued messages, 15 of them also visible as a
  plain user line.
* Codex keeps no queue in its rollout at all (a census of 194 rollouts /
  208,777 records / 28 distinct record types found nothing queue-shaped); it
  lives in ``$CODEX_HOME/queue_1.sqlite``, table ``queued_items``.

The two traps each get their own test, because both are silent: ordering by the
record's own timestamp files a message back among the work that answered the
PREVIOUS prompt, and letting a queued peer relay or task notification through
the human path inflates ``human_turns``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from grove.core.agents import QueuedMessage
from grove.core.agents.claude_code import (
    ClaudeCodeAdapter,
    _Record,
    _sort_key,
    _stamp_deliveries,
    _TranscriptParser,
)
from grove.core.agents.codex import CodexAdapter
from grove.core.agents.generic import GenericAdapter

# ── record builders (real shapes, sanitized) ───────────────────────────────


def _queued(
    prompt: str,
    *,
    at: str,
    mode: str = "prompt",
    origin: str | None = "human",
) -> dict[str, Any]:
    """A delivered ``queued_command`` attachment.

    ``origin`` of ``None`` reproduces the shape a ``task-notification`` always
    has on-host: the key is ABSENT, never a null or an empty kind.
    """
    attachment: dict[str, Any] = {"type": "queued_command", "prompt": prompt, "commandMode": mode}
    if origin is not None:
        attachment["origin"] = {"kind": origin}
    attachment["timestamp"] = at
    return {
        "type": "attachment",
        "attachment": attachment,
        "uuid": f"att-{at}",
        "timestamp": at,
        "cwd": "/w",
        "sessionId": "s",
    }


def _op(operation: str, *, at: str, content: str | None = None) -> dict[str, Any]:
    """A ``queue-operation`` record. A real ``dequeue`` carries NO content."""
    raw: dict[str, Any] = {"type": "queue-operation", "operation": operation, "timestamp": at}
    if content is not None:
        raw["content"] = content
    return raw


def _assistant(text: str, *, at: str, uuid: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": at,
        "message": {
            "id": f"msg-{uuid}",
            "model": "claude-test",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        },
    }


def _user(text: str, *, at: str, uuid: str) -> dict[str, Any]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": at,
        "message": {"role": "user", "content": text},
    }


def _parse(raws: list[dict[str, Any]]) -> _TranscriptParser:
    """Records folded, delivery-stamped and sorted exactly as ``_read`` does."""
    records = [_Record(raw=raw, index=i) for i, raw in enumerate(raws)]
    _stamp_deliveries(records)
    records.sort(key=_sort_key)
    return _TranscriptParser(records)


# ── classification ─────────────────────────────────────────────────────────


def test_a_queued_human_prompt_is_a_human_turn() -> None:
    """The whole point: a message typed at a busy agent is a real user turn,
    though it is not a ``type:"user"`` record."""
    rec = _Record(raw=_queued("do the thing", at="2026-08-01T10:00:00Z"), index=0)

    assert rec.is_queued_prompt
    assert rec.is_human_turn
    assert rec.text() == "do the thing"


def test_a_missing_origin_is_never_human() -> None:
    """``origin`` is absent on every real ``task-notification`` (999/999), so
    defaulting it to human would file every background notice as a user turn.
    The mode is checked FIRST, the origin second — a ``prompt`` with no origin
    at all is still not a human."""
    orphan = _Record(raw=_queued("...", at="2026-08-01T10:00:00Z", origin=None), index=0)

    assert not orphan.is_queued_prompt
    assert not orphan.is_human_turn


def test_a_queued_peer_relay_routes_to_the_teammate_home() -> None:
    """``origin.kind == "peer"`` is the queued form of a ``<teammate-message>``,
    and is classified by that ORIGIN rather than by an envelope marker: the
    queued form wears ``<agent-message from=…>`` instead, and one observed
    record wears no envelope at all."""
    rec = _Record(
        raw=_queued(
            '<agent-message from="reviewer">the diff looks fine</agent-message>',
            at="2026-08-01T10:00:00Z",
            origin="peer",
        ),
        index=0,
    )

    assert rec.is_queued_peer_message
    assert rec.is_teammate_message
    assert not rec.is_human_turn
    message = rec.to_message()
    assert message is not None
    assert message.role == "notification"
    # The body survives — a placeholder would lose the only content there is.
    assert "the diff looks fine" in message.text()


def test_a_queued_task_notification_routes_to_the_notification_home() -> None:
    rec = _Record(
        raw=_queued(
            "<task-notification>\n<task-id>t1</task-id>\n<summary>build done</summary>\n"
            "</task-notification>",
            at="2026-08-01T10:00:00Z",
            mode="task-notification",
            origin=None,
        ),
        index=0,
    )

    assert rec.is_queued_task_notification
    assert rec.is_task_notification
    assert not rec.is_human_turn
    message = rec.to_message()
    assert message is not None
    assert message.role == "notification"
    assert "build done" in message.text()


def test_neither_non_human_queued_form_inflates_the_turn_count() -> None:
    """The regression the marker set exists to prevent, at the counter that
    shows it: three queued records, exactly one of them a turn."""
    activity = _parse(
        [
            _queued("a real steer", at="2026-08-01T10:00:00Z"),
            _queued("relayed", at="2026-08-01T10:00:01Z", origin="peer"),
            _queued(
                "<task-notification></task-notification>",
                at="2026-08-01T10:00:02Z",
                mode="task-notification",
                origin=None,
            ),
        ]
    ).activity()

    assert activity.human_turns == 1


# ── the ordering trap ──────────────────────────────────────────────────────


def test_a_queued_prompt_orders_at_DELIVERY_not_at_its_own_timestamp() -> None:
    """The causal-lie test.

    The user types at 10:00:00 while the agent is mid-reply; the harness
    delivers it at 10:00:42, after the reply that answered the PREVIOUS prompt.
    The record's own timestamp is the SEND instant, so sorting by it files the
    steer BEFORE that reply — a transcript that reads as though the agent
    answered a question it had not been asked.
    """
    turns = _parse(
        [
            _user("first prompt", at="2026-08-01T09:59:00Z", uuid="u1"),
            # The user hits enter here, mid-reply…
            _op("enqueue", at="2026-08-01T10:00:00Z", content="second prompt"),
            _assistant("answering the FIRST prompt", at="2026-08-01T10:00:30Z", uuid="a1"),
            # …and the harness pops and delivers it only now.
            _op("remove", at="2026-08-01T10:00:42Z", content="second prompt"),
            _queued("second prompt", at="2026-08-01T10:00:00Z"),
            _assistant("answering the SECOND prompt", at="2026-08-01T10:01:00Z", uuid="a2"),
        ]
    ).turns()

    assert [t.user_text for t in turns] == ["first prompt", "second prompt"]
    # The reply to the first prompt stayed in the first turn.
    assert [e.text for e in turns[0].entries] == ["answering the FIRST prompt"]
    assert [e.text for e in turns[1].entries] == ["answering the SECOND prompt"]


def test_the_two_clocks_are_both_carried_and_are_not_the_same_one() -> None:
    """``started_at`` orders the conversation; ``sent_at`` says when the human
    actually pressed enter. Discarding either loses a real fact."""
    turns = _parse(
        [
            _op("enqueue", at="2026-08-01T10:00:00Z", content="queued steer"),
            _assistant("still busy", at="2026-08-01T10:00:30Z", uuid="a1"),
            _op("remove", at="2026-08-01T10:00:42Z", content="queued steer"),
            _queued("queued steer", at="2026-08-01T10:00:00Z"),
        ]
    ).turns()

    (_continuation, steer) = turns
    assert steer.started_at == datetime(2026, 8, 1, 10, 0, 42, tzinfo=UTC)
    assert steer.sent_at == datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)


def test_an_ordinary_turn_carries_no_sent_at() -> None:
    """A duplicate of ``started_at`` would invite a client to render a wait that
    was never measured."""
    (turn,) = _parse(
        [_user("typed at an idle agent", at="2026-08-01T10:00:00Z", uuid="u1")]
    ).turns()

    assert turn.started_at is not None
    assert turn.sent_at is None


# ── the pending queue (Claude) ─────────────────────────────────────────────


def test_enqueue_then_nothing_leaves_the_message_pending() -> None:
    queued = _parse(
        [
            _op("enqueue", at="2026-08-01T10:00:00Z", content="waiting one"),
            _op("enqueue", at="2026-08-01T10:00:05Z", content="waiting two"),
        ]
    ).pending_queue()

    assert queued == (
        QueuedMessage(
            text="waiting one", sent_at=datetime(2026, 8, 1, 10, 0, tzinfo=UTC), position=0
        ),
        QueuedMessage(
            text="waiting two", sent_at=datetime(2026, 8, 1, 10, 0, 5, tzinfo=UTC), position=1
        ),
    )


def test_a_delivered_message_leaves_the_queue() -> None:
    queued = _parse(
        [
            _op("enqueue", at="2026-08-01T10:00:00Z", content="delivered"),
            _op("enqueue", at="2026-08-01T10:00:01Z", content="still waiting"),
            _op("remove", at="2026-08-01T10:00:42Z", content="delivered"),
            _queued("delivered", at="2026-08-01T10:00:00Z"),
        ]
    ).pending_queue()

    assert [m.text for m in queued] == ["still waiting"]


def test_the_delivery_attachment_wins_over_a_contentless_dequeue() -> None:
    """A real ``dequeue`` names nothing (273/273 on-host), so FIFO is an
    assumption. Where the assumption could disagree with a WITNESS to an actual
    delivery, the witness wins: here the harness delivered the SECOND message,
    and a bare FIFO pop would have dropped the first."""
    queued = _parse(
        [
            _op("enqueue", at="2026-08-01T10:00:00Z", content="first in"),
            _op("enqueue", at="2026-08-01T10:00:01Z", content="second in"),
            _op("dequeue", at="2026-08-01T10:00:42Z"),
            _queued("second in", at="2026-08-01T10:00:01Z"),
        ]
    ).pending_queue()

    assert [m.text for m in queued] == ["first in"]


def test_a_contentless_dequeue_with_no_witness_pops_the_front() -> None:
    """With nothing to witness the delivery, FIFO is the honest assumption —
    and it is the only one the record supports."""
    queued = _parse(
        [
            _op("enqueue", at="2026-08-01T10:00:00Z", content="first in"),
            _op("enqueue", at="2026-08-01T10:00:01Z", content="second in"),
            _op("dequeue", at="2026-08-01T10:00:42Z"),
        ]
    ).pending_queue()

    assert [m.text for m in queued] == ["second in"]


def test_pop_all_clears_the_queue() -> None:
    """The harness emits one ``popAll`` per item it drops; clearing on the first
    makes the rest no-ops, which is the same end state."""
    queued = _parse(
        [
            _op("enqueue", at="2026-08-01T10:00:00Z", content="one"),
            _op("enqueue", at="2026-08-01T10:00:01Z", content="two"),
            _op("popAll", at="2026-08-01T10:00:02Z", content="one"),
            _op("popAll", at="2026-08-01T10:00:03Z", content="two"),
        ]
    ).pending_queue()

    assert queued == ()


def test_the_claude_adapter_reads_the_queue_off_the_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the public seam — the same incremental read every
    other projection uses, so no new file and no new scan."""
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    adapter = ClaudeCodeAdapter()
    adapter.clear_caches()

    cwd = tmp_path / "work"
    sid = "33333333-3333-4333-8333-333333333333"
    folder = cfg / "projects" / "encoded"
    folder.mkdir(parents=True)
    rows = [
        {
            "type": "user",
            "uuid": "u0",
            "timestamp": "2026-08-01T09:00:00Z",
            "cwd": str(cwd),
            "sessionId": sid,
            "message": {"role": "user", "content": "go"},
        },
        _op("enqueue", at="2026-08-01T10:00:00Z", content="hold this"),
    ]
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    assert [m.text for m in adapter.pending_queue(cwd, sid)] == ["hold this"]


# ── Codex: a SQLite store, and every absence is UNSUPPORTED ────────────────


@pytest.fixture
def codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _queue_db(home: Path, rows: list[tuple[str, str, str, int, int]]) -> None:
    conn = sqlite3.connect(home / "queue_1.sqlite")
    conn.execute(
        "CREATE TABLE queued_items (id TEXT PRIMARY KEY NOT NULL, thread_id TEXT NOT NULL, "
        "payload_json TEXT NOT NULL, queue_order INTEGER NOT NULL, "
        "created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL)"
    )
    conn.executemany(
        "INSERT INTO queued_items VALUES (?,?,?,?,?,0)",
        rows,
    )
    conn.commit()
    conn.close()


def test_codex_reads_its_queue_in_the_harness_order(codex_home: Path) -> None:
    """``queue_order`` is the harness's own ordering, not insertion order into
    the table — so the row inserted first can legitimately come second."""
    _queue_db(
        codex_home,
        [
            ("b", "thread-1", json.dumps({"text": "second"}), 2, 1_770_000_002_000),
            ("a", "thread-1", json.dumps("first"), 1, 1_770_000_001_000),
            ("z", "thread-2", json.dumps({"text": "another thread"}), 1, 1_770_000_003_000),
        ],
    )

    queued = CodexAdapter().pending_queue(Path("/w"), "thread-1")

    assert [(m.position, m.text) for m in queued] == [(0, "first"), (1, "second")]
    assert queued[0].sent_at == datetime.fromtimestamp(1_770_000_001, tz=UTC)


def test_codex_hands_back_an_unrecognized_payload_verbatim(codex_home: Path) -> None:
    """The column's shape is UNOBSERVED — the table existed and was empty every
    time it was read on the reference host — so anything that is not one of the
    two no-interpretation shapes is shown rather than silently dropped."""
    _queue_db(codex_home, [("a", "t", "not json at all", 1, 1_770_000_001_000)])

    assert [m.text for m in CodexAdapter().pending_queue(Path("/w"), "t")] == ["not json at all"]


def test_codex_treats_a_missing_store_as_nothing_queued(codex_home: Path) -> None:
    """A host that has never run Codex, or a build predating the queue, is not
    an error — it is a provider with nothing to report."""
    assert CodexAdapter().pending_queue(Path("/w"), "t") == ()


def test_codex_treats_a_missing_table_as_nothing_queued(codex_home: Path) -> None:
    conn = sqlite3.connect(codex_home / "queue_1.sqlite")
    conn.execute("CREATE TABLE something_else (x TEXT)")
    conn.commit()
    conn.close()

    assert CodexAdapter().pending_queue(Path("/w"), "t") == ()


def test_codex_resolves_the_store_through_the_shared_config_root(
    codex_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One definition of where Codex lives, so a relocated ``$CODEX_HOME`` moves
    the rollouts, the prompts and the queue together."""
    _queue_db(codex_home, [("a", "t", json.dumps({"text": "here"}), 1, 1_770_000_001_000)])
    elsewhere = tmp_path / "relocated"
    elsewhere.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(elsewhere))

    assert CodexAdapter().pending_queue(Path("/w"), "t") == ()


# ── the adapters that honestly have no queue ───────────────────────────────


def test_a_shell_reports_no_queue_and_says_so() -> None:
    """``()`` plus ``reports_queue=False`` — the pair is what lets the wire say
    "no idea" instead of claiming an empty queue."""
    adapter = GenericAdapter()

    assert adapter.pending_queue(Path("/w"), "s") == ()
    assert adapter.reports_queue is False


def test_both_filesystem_harnesses_declare_that_they_can_report() -> None:
    assert ClaudeCodeAdapter().reports_queue is True
    assert CodexAdapter().reports_queue is True
