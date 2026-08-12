"""Compaction-boundary detection — the provider-neutral ``CompactionBoundary``
contract, both adapters' native shapes, and the wire mirror.

Every payload here mirrors a REAL on-host record, and the two facts that are
easiest to get wrong are the two the real corpus taught (36 Claude sessions / 55
boundaries, 40 Codex rollouts / 132 boundaries):

* Claude's summary record carries an EARLIER timestamp than the boundary it
  belongs to (55/55, by up to 1.6 s) while being written AFTER it, so by the time
  the parser has time-sorted its records the summary sits *before* the boundary
  in 47 of 55 cases. The join is therefore by ``parentUuid`` → boundary ``uuid``
  (55/55), and the fixtures below are ordered to make a positional scan fail.
* ``cumulativeDroppedTokens`` is a SESSION-RUNNING TOTAL. The per-event delta is
  it minus the previous boundary's total, which is verified against the same
  record's independent ``preTokens - postTokens`` on all 55.

Codex's half is mostly about what is NOT there: no trigger field in any version
from 0.93.0 to 0.147.0, no token accounting, and an encrypted replacement history
whose ``payload.message`` measured empty on 132 of 132 records.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.agents import CompactionBoundary, DigestEntry
from grove.core.agents.claude_code import _Record, _TranscriptParser
from grove.core.agents.codex import _RolloutLine, _RolloutParser
from grove.core.contracts.questions import _ENTRY_TEXT_CAP
from grove.core.contracts.sessions import DigestEntryView

# ── the provider-neutral contract ────────────────────────────────────────────


def test_headline_names_a_recorded_trigger_and_never_invents_one() -> None:
    """``text`` is what a role-unaware consumer reads, so it is defined once on
    the payload rather than per adapter. A harness that records no trigger
    (Codex) says only that a compaction happened — naming one would be
    indistinguishable from Claude's measured value."""
    at = datetime(2026, 8, 11, tzinfo=UTC)
    assert CompactionBoundary("auto", at, 1, "s").headline() == "Context compacted (automatic)"
    assert CompactionBoundary("manual", at, 1, "s").headline() == "Context compacted (manual)"
    assert CompactionBoundary(None, at, None, "").headline() == "Context compacted"


# ── Claude Code ──────────────────────────────────────────────────────────────


def _boundary(
    uuid: str, ts: str, *, trigger: str = "manual", cumulative: int | None = 558034
) -> dict[str, object]:
    """A real-shaped ``type:"system"`` / ``subtype:"compact_boundary"`` record."""
    metadata: dict[str, object] = {"trigger": trigger, "preTokens": 600000, "postTokens": 41966}
    if cumulative is not None:
        metadata["cumulativeDroppedTokens"] = cumulative
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "uuid": uuid,
        "timestamp": ts,
        "isSidechain": False,
        "compactMetadata": metadata,
    }


def _summary(parent: str, ts: str, text: str = "Here is the summary so far.") -> dict[str, object]:
    """The replacement-summary record: ``type:"user"``, ``isCompactSummary``, and
    a ``parentUuid`` naming its boundary."""
    return {
        "type": "user",
        "uuid": f"sum-{parent}",
        "parentUuid": parent,
        "isCompactSummary": True,
        "timestamp": ts,
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }


def _user(uuid: str, ts: str, text: str) -> dict[str, object]:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": ts,
        "isSidechain": False,
        "message": {"role": "user", "content": text},
    }


def _assistant(uuid: str, ts: str, text: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": ts,
        "isSidechain": False,
        "message": {
            "id": f"msg-{uuid}",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": text}],
        },
    }


def _parse(raws: list[dict[str, object]]) -> _TranscriptParser:
    """Records in the order the parser really sees them — TIME-SORTED, which is
    what makes the summary land before its boundary."""
    records = [_Record(raw, index) for index, raw in enumerate(raws)]
    records.sort(key=lambda r: ((r.timestamp.timestamp() if r.timestamp else 0.0), r.index))
    return _TranscriptParser(records)


def test_boundary_claims_its_summary_by_parent_uuid_not_by_position() -> None:
    """The regression the real corpus exposed: the summary's timestamp is EARLIER
    than its boundary's, so after the parser's time-sort it precedes the
    boundary. A forward scan finds nothing; the ``parentUuid`` join finds it."""
    parser = _parse(
        [
            _user("u1", "2026-08-11T10:00:00.000Z", "do the thing"),
            _boundary("b1", "2026-08-11T10:05:01.600Z"),
            # Written AFTER the boundary, stamped 1.6 s BEFORE it — the real shape.
            _summary("b1", "2026-08-11T10:05:00.000Z", "The story so far."),
        ]
    )
    # Reaching into the record list is the point: the ORDERING is what is tested.
    records = parser._records
    assert [r.is_compact_summary for r in records].index(True) < [
        r.is_compact_boundary for r in records
    ].index(True), "fixture must reproduce the inverted order, or the test proves nothing"

    (message,) = [m for m in parser.messages() if m.role == "compaction"]
    assert message.compaction is not None
    assert message.compaction.summary == "The story so far."


def test_boundary_message_is_contentless_so_text_readers_skip_it() -> None:
    """The payload cannot ride content blocks, and carrying the summary as text
    would push tens of KB into every consumer that reads a message for prose (the
    trace exporter's chat parts, the final-result scan). Empty content is what
    makes putting the event on the spine free for them."""
    parser = _parse(
        [_boundary("b1", "2026-08-11T10:05:01Z"), _summary("b1", "2026-08-11T10:05:00Z")]
    )
    (message,) = [m for m in parser.messages() if m.role == "compaction"]
    assert message.content == ()
    assert message.text() == ""


def test_dropped_tokens_is_a_per_event_delta_of_a_running_total() -> None:
    """``cumulativeDroppedTokens`` grows across the session, so the first
    boundary's delta is its own total (nothing was dropped before it) and every
    later one is the difference. Reporting the raw total would inflate each
    later boundary by the whole session's history."""
    parser = _parse(
        [
            _boundary("b1", "2026-08-11T10:00:01Z", cumulative=744_793),
            _summary("b1", "2026-08-11T10:00:00Z"),
            _boundary("b2", "2026-08-11T11:00:01Z", cumulative=1_372_448),
            _summary("b2", "2026-08-11T11:00:00Z"),
            _boundary("b3", "2026-08-11T12:00:01Z", cumulative=1_778_777),
            _summary("b3", "2026-08-11T12:00:00Z"),
        ]
    )
    boundaries = [m.compaction for m in parser.messages() if m.role == "compaction"]
    assert [b.dropped_tokens for b in boundaries if b] == [744_793, 627_655, 406_329]


def test_an_unusable_total_yields_no_count_rather_than_a_wrong_one() -> None:
    """A missing total, and a total that went BACKWARDS (the counter restarted
    under us), are both "cannot tell" — never a fabricated or negative count."""
    parser = _parse(
        [
            _boundary("b1", "2026-08-11T10:00:01Z", cumulative=None),
            _boundary("b2", "2026-08-11T11:00:01Z", cumulative=900_000),
            _boundary("b3", "2026-08-11T12:00:01Z", cumulative=10),
        ]
    )
    boundaries = [m.compaction for m in parser.messages() if m.role == "compaction"]
    assert [b.dropped_tokens for b in boundaries if b] == [None, 900_000, None]


def test_only_the_two_measured_triggers_pass_through() -> None:
    """``manual``/``auto`` are exactly what Claude Code writes (46/9 over the real
    corpus). A third spelling is an unmeasured provider change, so it reads as
    absent rather than being coerced into one of the two."""
    parser = _parse(
        [
            _boundary("b1", "2026-08-11T10:00:01Z", trigger="auto"),
            _boundary("b2", "2026-08-11T11:00:01Z", trigger="manual"),
            _boundary("b3", "2026-08-11T12:00:01Z", trigger="scheduled"),
        ]
    )
    boundaries = [m.compaction for m in parser.messages() if m.role == "compaction"]
    assert [b.trigger for b in boundaries if b] == ["auto", "manual", None]


def test_the_boundary_rides_the_turn_it_happened_in_with_its_payload() -> None:
    """Like a notification, a compaction is an entry INSIDE the open turn rather
    than a turn of its own — the harness cut the context mid-conversation."""
    parser = _parse(
        [
            _user("u1", "2026-08-11T10:00:00Z", "start"),
            _assistant("a1", "2026-08-11T10:01:00Z", "working"),
            _boundary("b1", "2026-08-11T10:02:01Z", trigger="auto"),
            _summary("b1", "2026-08-11T10:02:00Z", "Recap of the work so far."),
            _assistant("a2", "2026-08-11T10:03:00Z", "continuing"),
        ]
    )
    (turn,) = parser.turns()
    assert turn.user_text == "start"
    (entry,) = [e for e in turn.entries if e.role == "compaction"]
    assert entry.text == "Context compacted (automatic)"
    assert entry.compaction is not None
    assert entry.compaction.summary == "Recap of the work so far."


def test_the_digest_carries_the_marker_and_strips_the_summary() -> None:
    """The digest is the cheap LLM-interpreter seam and strips payloads by
    design; a compaction summary measured 13.9-55.3 KB on-host, which is the
    largest single thing a transcript carries."""
    parser = _parse(
        [
            _boundary("b1", "2026-08-11T10:02:01Z"),
            _summary("b1", "2026-08-11T10:02:00Z", "x" * 40_000),
        ]
    )
    (entry,) = [e for e in parser.digest().entries if e.role == "compaction"]
    assert entry.text == "Context compacted (manual)"
    assert entry.compaction is None


def test_a_compaction_summary_is_still_not_a_human_turn() -> None:
    """It is the harness talking. The boundary now claims the text as its payload,
    which must not promote the record into the turn count."""
    parser = _parse(
        [
            _user("u1", "2026-08-11T10:00:00Z", "the only real prompt"),
            _boundary("b1", "2026-08-11T10:02:01Z"),
            _summary("b1", "2026-08-11T10:02:00Z"),
        ]
    )
    assert parser.activity().human_turns == 1
    assert [m.role for m in parser.messages() if m.role == "user"] == ["user"]


# ── Codex ────────────────────────────────────────────────────────────────────


def _compacted(ts: str, message: str = "") -> dict[str, object]:
    """A real-shaped Codex ``compacted`` record: TOP-LEVEL type, empty
    ``payload.message``, encrypted replacement history beside it."""
    return {
        "timestamp": ts,
        "type": "compacted",
        "payload": {"message": message, "replacement_history": [{"type": "message"}]},
    }


def _codex(raws: list[dict[str, object]]) -> _RolloutParser:
    return _RolloutParser([_RolloutLine(raw, index) for index, raw in enumerate(raws)])


def test_codex_records_a_boundary_and_nothing_else_about_it() -> None:
    """No trigger field exists in any Codex version (0.93.0 → 0.147.0), no
    per-compaction token accounting exists, and the replacement history is
    encrypted — so three of the four fields are honestly absent. ``trigger`` must
    read ``None``, never a guessed ``auto``."""
    parser = _codex([_compacted("2026-08-09T20:14:18.993Z")])
    (message,) = [m for m in parser.messages() if m.role == "compaction"]
    assert message.content == ()
    boundary = message.compaction
    assert boundary is not None
    assert boundary.trigger is None
    assert boundary.dropped_tokens is None
    assert boundary.summary == ""
    assert boundary.at == datetime(2026, 8, 9, 20, 14, 18, 993_000, tzinfo=UTC)


def test_codex_surfaces_a_message_the_day_one_is_written() -> None:
    """``payload.message`` measured empty on 132/132 real records, but the field
    is read rather than hard-coded to ``""`` — reading it costs nothing and is
    what would surface a future Codex that fills it."""
    parser = _codex([_compacted("2026-08-09T20:14:18.993Z", "a real summary")])
    (message,) = [m for m in parser.messages() if m.role == "compaction"]
    assert message.compaction is not None
    assert message.compaction.summary == "a real summary"


def test_the_content_free_event_msg_mirror_is_never_the_key() -> None:
    """``event_msg``/``context_compacted`` carries nothing and is ABSENT in
    0.147.0 — the one place the dual-record rule's ``event_msg`` half is not
    merely redundant but wrong. Keying on it goes blind on the current release."""
    parser = _codex(
        [
            {
                "timestamp": "2026-08-09T20:14:18.993Z",
                "type": "event_msg",
                "payload": {"type": "context_compacted"},
            }
        ]
    )
    assert [m for m in parser.messages() if m.role == "compaction"] == []


def test_codex_boundary_rides_the_turn_stream() -> None:
    parser = _codex(
        [
            {
                "timestamp": "2026-08-09T20:00:00.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "go"}],
                },
            },
            _compacted("2026-08-09T20:14:18.993Z"),
        ]
    )
    (turn,) = parser.turns()
    (entry,) = [e for e in turn.entries if e.role == "compaction"]
    assert entry.text == "Context compacted"
    assert entry.compaction is not None


# ── the wire mirror ──────────────────────────────────────────────────────────


def test_the_view_keeps_each_absence_distinguishable() -> None:
    """A client must be able to tell "this harness records no trigger" from
    either trigger value, and "no per-event count" from a zero."""
    view = DigestEntryView.from_entry(
        DigestEntry(
            "compaction",
            "Context compacted",
            compaction=CompactionBoundary(None, None, None, ""),
        )
    )
    assert view.role == "compaction"
    assert view.compaction is not None
    assert view.compaction.trigger is None
    assert view.compaction.dropped_tokens is None
    assert view.compaction.summary == ""
    assert view.model_dump()["compaction"]["trigger"] is None


def test_the_view_carries_a_real_boundary_whole_and_caps_the_summary() -> None:
    """A real summary is chat-scale prose, so it takes the shared entry cap
    rather than the diff-sized one — it rides the same per-turn payload as every
    other entry and a turn can hold several boundaries."""
    at = datetime(2026, 8, 11, 10, 2, 1, tzinfo=UTC)
    view = DigestEntryView.from_entry(
        DigestEntry(
            "compaction",
            "Context compacted (automatic)",
            compaction=CompactionBoundary("auto", at, 627_655, "y" * 30_000),
        )
    )
    assert view.compaction is not None
    assert view.compaction.trigger == "auto"
    assert view.compaction.at == at
    assert view.compaction.dropped_tokens == 627_655
    assert len(view.compaction.summary) == _ENTRY_TEXT_CAP


def test_every_other_role_leaves_the_payload_null() -> None:
    """The recipe's invariant: a structured payload is set only for its own role,
    so an old client that ignores the field degrades to the one-liner."""
    view = DigestEntryView.from_entry(DigestEntry("assistant", "just prose"))
    assert view.compaction is None
