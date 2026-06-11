"""Pure-unit coverage for the Claude Code adapter + the tool-agnostic model.

Fixtures under ``fixtures/`` are sanitized, hand-built from real on-host
transcript shapes. They pin the parsing invariants that are the crux of the
epic: the real-human-turn filter (the "48 lines, 5 turns" case), resume/fork
de-dup, sidechain + marker exclusion, and survival of a truncated final line.
No network, no tmux, no real ``~/.claude``.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from grove.core.agents import AgentActivityState, get_adapter
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "basic.jsonl"
RESUME = FIXTURES / "basic_resume.jsonl"
NOISE = FIXTURES / "noise.jsonl"


@pytest.fixture
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


# ─── parsing metrics ────────────────────────────────────────────────────────


def test_basic_session_metrics(adapter: ClaudeCodeAdapter) -> None:
    act = adapter.parse_activity([BASIC])

    assert act.human_turns == 2
    assert act.replies_per_turn == (3, 2)
    assert act.assistant_replies == 5
    assert act.assistant_replies == sum(act.replies_per_turn)
    assert act.human_turns == len(act.replies_per_turn)
    assert act.tool_calls == 3
    assert act.model == "claude-opus-4-8"
    # cache reads/writes fold into "in"; outputs sum into "out".
    assert act.tokens_in == 1500
    assert act.tokens_out == 150
    assert act.title == "Add service healthcheck endpoint"
    assert act.current_task == "Now write a test for the healthcheck endpoint"
    assert act.state is AgentActivityState.WAITING  # tail assistant ended its turn
    assert act.needs_attention is True
    assert act.last_event_at == datetime.fromisoformat("2026-06-01T10:00:10.000Z")


def test_resume_dedups_overlapping_records(adapter: ClaudeCodeAdapter) -> None:
    """basic + its resume share one assistant line (same id+requestId): count once."""
    act = adapter.parse_activity([BASIC, RESUME])

    assert act.human_turns == 3
    assert act.replies_per_turn == (3, 2, 1)
    assert act.assistant_replies == 6
    assert act.tool_calls == 4
    # The duplicated a5 (500 in / 50 out) is not double-counted; only a6 is added.
    assert act.tokens_in == 2100
    assert act.tokens_out == 210
    assert act.state is AgentActivityState.WORKING  # tail is a tool_use
    assert act.needs_attention is False


def test_noise_excludes_machinery_and_survives_truncation(adapter: ClaudeCodeAdapter) -> None:
    """Sidechain, slash-command, caveat, and compaction lines are not human turns;
    a truncated final line does not abort the file."""
    act = adapter.parse_activity([NOISE])

    assert act.human_turns == 1  # only "Fix the flaky test in the parser"
    assert act.replies_per_turn == (2,)
    assert act.tool_calls == 1
    # Sidechain assistant's 1000/100 tokens are excluded from main-thread totals.
    assert act.tokens_in == 130
    assert act.tokens_out == 13
    assert act.state is AgentActivityState.WAITING


def test_string_boolean_sidechain_is_coerced(adapter: ClaudeCodeAdapter, tmp_path: Path) -> None:
    """Defensive: a sidechain flag serialized as the string ``"true"`` must still
    exclude the line (a naive ``bool("true")`` would too, but ``bool("false")``
    would wrongly include — this pins the coercion)."""
    transcript = tmp_path / "strbool.jsonl"
    transcript.write_text(
        '{"type":"user","uuid":"x","timestamp":"2026-06-03T10:00:00.000Z",'
        '"isSidechain":"false","message":{"role":"user","content":"real turn"}}\n'
        '{"type":"user","uuid":"y","timestamp":"2026-06-03T10:00:01.000Z",'
        '"isSidechain":"true","message":{"role":"user","content":"sidechain turn"}}\n',
        encoding="utf-8",
    )
    act = adapter.parse_activity([transcript])
    assert act.human_turns == 1  # the "true"-string sidechain line is excluded


def test_empty_paths_yield_unknown(adapter: ClaudeCodeAdapter) -> None:
    act = adapter.parse_activity([])
    assert act.state is AgentActivityState.UNKNOWN
    assert act.human_turns == 0
    assert act.needs_attention is False


def test_interpreted_status_reserved_but_unset(adapter: ClaudeCodeAdapter) -> None:
    """#20 seam: the field exists and defaults to None (no interpreter wired)."""
    assert adapter.parse_activity([BASIC]).interpreted_status is None


def test_missing_file_is_best_effort(adapter: ClaudeCodeAdapter, tmp_path: Path) -> None:
    act = adapter.parse_activity([tmp_path / "does-not-exist.jsonl"])
    assert act.state is AgentActivityState.UNKNOWN


# ─── digest (the #20 seam) ──────────────────────────────────────────────────


def test_digest_skeleton_excludes_tool_results(adapter: ClaudeCodeAdapter) -> None:
    digest = adapter.transcript_digest([BASIC])
    roles = [e.role for e in digest.entries]
    assert "user" in roles
    assert "tool" in roles  # tool_use blocks become TOOL(name) entries
    # No entry carries tool_result payloads.
    assert all("tool_result" not in e.text for e in digest.entries)
    tool_entries = [e.text for e in digest.entries if e.role == "tool"]
    assert "Edit" in tool_entries
    assert "Bash" in tool_entries


# ─── locate_transcripts (filesystem, hermetic) ──────────────────────────────


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandboxed Claude config dir; ``CLAUDE_CONFIG_DIR`` + ``Path.home`` both
    point inside ``tmp_path`` so locate never touches the real ``~/.claude``."""
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def test_locate_finds_main_and_subagents(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    cwd = Path("/home/kk/work/svc")
    sid = "33333333-3333-4333-8333-333333333333"
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    main = folder / f"{sid}.jsonl"
    main.write_text('{"type":"user","cwd":"/home/kk/work/svc"}\n', encoding="utf-8")
    sub_dir = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd) / sid / "subagents"
    sub_dir.mkdir(parents=True)
    sub = sub_dir / "agent-abc.jsonl"
    sub.write_text('{"type":"assistant"}\n', encoding="utf-8")

    found = adapter.locate_transcripts(cwd, sid)
    assert main in found
    assert sub in found
    assert found.index(main) < found.index(sub)  # main thread first


def test_locate_missing_returns_empty(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    del claude_home
    found = adapter.locate_transcripts(Path("/nowhere"), "44444444-4444-4444-8444-444444444444")
    assert found == []


def test_discover_orders_most_recent_first(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """Discovery returns ids newest-first (by transcript mtime), so a workspace
    with no minted id adopts the *live* session — not an arbitrary alphabetical
    one. The older id sorts first alphabetically, so a stable result proves the
    mtime ordering rather than a coincidence."""
    cwd = Path("/home/kk/work/multi")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    older = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"  # alphabetically first
    newer = "ffffffff-ffff-4fff-8fff-ffffffffffff"  # alphabetically last
    for sid, mtime in ((older, 1000), (newer, 2000)):
        path = folder / f"{sid}.jsonl"
        path.write_text(f'{{"type":"user","cwd":"{cwd}"}}\n', encoding="utf-8")
        os.utime(path, (mtime, mtime))

    assert adapter.discover_sessions(cwd) == [newer, older]


def test_discover_skips_cwdless_preamble(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """Real transcripts open with cwd-less preamble lines (``mode``,
    ``file-history-snapshot``, ``summary``); the ``cwd`` first appears a few
    lines in. Discovery must scan past the preamble — keying off only line 0
    (the old behavior) returns ``None`` for every real transcript and finds
    nothing."""
    cwd = Path("/home/kk/work/preamble")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    sid = "55555555-5555-4555-8555-555555555555"
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"mode","mode":"default"}\n'
        '{"type":"file-history-snapshot","messageId":"x"}\n'
        f'{{"type":"user","cwd":"{cwd}","message":{{"role":"user","content":"hi"}}}}\n',
        encoding="utf-8",
    )

    assert adapter.discover_sessions(cwd) == [sid]


# ─── encode_cwd (the documented folder rule) ────────────────────────────────


def test_encode_cwd_replaces_every_non_alphanumeric() -> None:
    """The Agent SDK documents the encoding as *every* non-alphanumeric char →
    ``-`` — not just ``/`` ``.`` ``_``. A cwd with ``@``/``+``/space must still
    hit the fast path."""
    assert _ClaudeHome.encode_cwd(Path("/home/kk/my proj+v2@x")) == "-home-kk-my-proj-v2-x"
    assert _ClaudeHome.encode_cwd(Path("/home/kk/.claude_dir")) == "-home-kk--claude-dir"


# ─── list_sessions (summaries for the explorer) ─────────────────────────────


def _write_realistic_transcript(folder: Path, sid: str, cwd: Path, *, mtime: int) -> Path:
    """A transcript with the on-host shape: cwd-less preamble, ai-title, a
    leafUuid-only ``last-prompt`` (no text) AND one carrying text, ``gitBranch``
    on the records."""
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"last-prompt","leafUuid":"x","sessionId":"' + sid + '"}\n'
        '{"type":"mode","mode":"normal","sessionId":"' + sid + '"}\n'
        '{"type":"file-history-snapshot","messageId":"m1"}\n'
        '{"type":"ai-title","aiTitle":"Fix the widget","sessionId":"' + sid + '"}\n'
        '{"type":"user","uuid":"h1","timestamp":"2026-06-09T08:00:00.000Z",'
        '"isSidechain":false,"cwd":"' + str(cwd) + '","gitBranch":"feature/widget",'
        '"message":{"role":"user","content":"Please fix the widget"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-09T08:00:05.000Z",'
        '"isSidechain":false,"message":{"id":"m-1","role":"assistant","model":"claude-opus-4-8",'
        '"stop_reason":"end_turn","usage":{"input_tokens":10,"output_tokens":5},'
        '"content":[{"type":"text","text":"Fixed."}]}}\n'
        '{"type":"last-prompt","lastPrompt":"Please fix the widget",'
        '"leafUuid":"h1","sessionId":"' + sid + '"}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def test_list_sessions_builds_summaries_newest_first(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    cwd = Path("/home/kk/work/listing")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    older = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    newer = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    _write_realistic_transcript(folder, older, cwd, mtime=1_000)
    _write_realistic_transcript(folder, newer, cwd, mtime=2_000)

    summaries = adapter.list_sessions(cwd)

    assert [s.session_id for s in summaries] == [newer, older]
    top = summaries[0]
    assert top.adapter_kind == "claude_code"
    assert top.cwd == str(cwd)
    assert top.git_branch == "feature/widget"
    assert top.title == "Fix the widget"
    assert top.first_prompt == "Please fix the widget"
    # The leafUuid-only last-prompt is skipped; the text-bearing one wins.
    assert top.last_prompt == "Please fix the widget"
    assert top.size_bytes > 0
    assert top.modified_at is not None
    assert top.created_at == datetime.fromisoformat("2026-06-09T08:00:00.000Z")
    assert top.activity.human_turns == 1
    assert top.activity.state is AgentActivityState.WAITING


def test_list_sessions_empty_when_nothing_recorded(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    del claude_home
    assert adapter.list_sessions(Path("/nowhere")) == []


# ─── read_turns (the `sessions show` view) ──────────────────────────────────


def test_read_turns_groups_replies_under_each_prompt(adapter: ClaudeCodeAdapter) -> None:
    turns = adapter.read_turns([BASIC])

    assert len(turns) == 2
    first, second = turns
    assert first.user_text == "Add a healthcheck endpoint to the service"
    assert first.started_at == datetime.fromisoformat("2026-06-01T10:00:00.000Z")
    # Block order preserved: text, Edit, Bash, closing text.
    assert [(e.role, e.text) for e in first.entries] == [
        ("assistant", "I'll add the endpoint."),
        ("tool", "Edit"),
        ("tool", "Bash"),
        ("assistant", "Endpoint added."),
    ]
    assert second.user_text == "Now write a test for it"
    assert [e.role for e in second.entries] == ["tool", "assistant"]


def test_read_turns_last_window(adapter: ClaudeCodeAdapter) -> None:
    turns = adapter.read_turns([BASIC], last=1)
    assert len(turns) == 1
    assert turns[0].user_text == "Now write a test for it"
    assert adapter.read_turns([BASIC], last=0) == ()


def test_read_turns_leading_continuation_block(adapter: ClaudeCodeAdapter, tmp_path: Path) -> None:
    """Assistant records before any human turn (resumed/compacted head) collect
    under an empty-prompt turn instead of being dropped."""
    transcript = tmp_path / "cont.jsonl"
    transcript.write_text(
        '{"type":"assistant","uuid":"a1","timestamp":"2026-06-03T10:00:00.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant",'
        '"stop_reason":"end_turn","content":[{"type":"text","text":"Picking up."}]}}\n'
        '{"type":"user","uuid":"h1","timestamp":"2026-06-03T10:00:01.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"Continue please"}}\n',
        encoding="utf-8",
    )
    turns = adapter.read_turns([transcript])
    assert len(turns) == 2
    assert turns[0].user_text == ""
    assert turns[0].entries[0].text == "Picking up."
    assert turns[1].user_text == "Continue please"


# ─── registry + generic adapter ─────────────────────────────────────────────


def test_get_adapter_selects_claude_code() -> None:
    assert get_adapter("claude_code").kind == "claude_code"


def test_get_adapter_unknown_falls_back_to_generic() -> None:
    assert get_adapter("nonsense").kind == "generic"


def test_generic_adapter_is_benign() -> None:
    generic = get_adapter("generic")
    assert generic.launch_decoration("uuid") == []
    assert generic.locate_transcripts(Path("/x"), "uuid") == []
    assert generic.parse_activity([BASIC]).state is AgentActivityState.UNKNOWN
    assert generic.list_sessions(Path("/x")) == []
    assert generic.read_turns([BASIC]) == ()


def test_claude_launch_decoration() -> None:
    assert get_adapter("claude_code").launch_decoration("abc-123") == ["--session-id", "abc-123"]
