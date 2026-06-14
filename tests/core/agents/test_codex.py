"""Pure-unit coverage for the Codex CLI adapter.

The ``codex_basic.jsonl`` fixture is a sanitized slice of a real on-host rollout
(codex 0.125.0, 2026-04-28) — trimmed and scrubbed of host-private paths/urls,
but its *shape* is verbatim: ``session_meta`` head, the AGENTS.md +
``<environment_context>`` preamble user message, a real human turn, an opaque
encrypted ``reasoning`` record, a ``function_call``/``function_call_output``
pair, an assistant ``message``, the ``event_msg`` mirrors that MUST be ignored,
and a ``task_started``/``task_complete`` pair. It pins the crux invariants: the
real-human-turn filter (preamble excluded), dual-record dedup (the
``event_msg`` mirrors don't double the turn/reply), tool-call counting (call
side only), and the task-pairing status rule. No network, no real ``~/.codex``.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from grove.core.agents import AgentActivityState, get_adapter
from grove.core.agents.codex import CodexAdapter, _RolloutParser

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "codex_basic.jsonl"

# The id + cwd the fixture's session_meta records in-line.
BASIC_SID = "019dd5d5-60fb-7461-bd07-b6e8cf342726"
BASIC_CWD = Path("/home/dev/work/svc")


@pytest.fixture
def adapter() -> CodexAdapter:
    return CodexAdapter()


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandboxed Codex home; ``CODEX_HOME`` + ``Path.home`` both point inside
    ``tmp_path`` so the adapter never touches the real ``~/.codex``."""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _install(codex_home: Path, sid: str, source: Path, *, subdir: str = "2026/04/28") -> Path:
    """Drop a fixture rollout where ``locate``/``discover`` find it: under the
    date-partitioned ``sessions/`` tree, named ``rollout-<ts>-<uuid>.jsonl``."""
    target_dir = codex_home / "sessions" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"rollout-2026-04-28T13-43-44-{sid}.jsonl"
    shutil.copyfile(source, target)
    return target


# ─── parsing metrics (through the (cwd, session_id)-keyed surface) ──────────


def test_basic_session_metrics(adapter: CodexAdapter, codex_home: Path) -> None:
    _install(codex_home, BASIC_SID, BASIC)
    act = adapter.parse_activity(BASIC_CWD, BASIC_SID)

    # ONE genuine human turn: the developer message and the AGENTS.md +
    # <environment_context> preamble user message are both excluded; only the
    # real prompt counts. The event_msg/user_message mirror is NOT a response_item
    # so it never doubles the turn.
    assert act.human_turns == 1
    assert act.replies_per_turn == (1,)
    assert act.assistant_replies == 1
    assert act.assistant_replies == sum(act.replies_per_turn)
    assert act.human_turns == len(act.replies_per_turn)
    # ONE tool call: the function_call counts, its function_call_output does not.
    assert act.tool_calls == 1
    # Model comes from turn_context (session_meta carries no model on this host).
    assert act.model == "gpt-5.5"
    # token_count.info is null in this slice → tokens legitimately read 0.
    assert act.tokens_in == 0
    assert act.tokens_out == 0
    assert act.current_task == "Add a healthcheck endpoint to the service"
    # task_started turn_id is matched by task_complete → the human has the move.
    assert act.state is AgentActivityState.WAITING
    assert act.needs_attention is True
    assert act.last_event_at == datetime.fromisoformat("2026-04-28T20:51:44.709Z")


def test_event_msg_mirrors_do_not_double_count(adapter: CodexAdapter, codex_home: Path) -> None:
    """The crux dual-record trap: Codex records each message twice — once as a
    ``response_item`` and again as an ``event_msg`` (``user_message`` /
    ``agent_message``). Counting from both doubles every turn and reply. The
    fixture carries both mirrors, so a correct parse reads exactly 1 turn / 1
    reply (not 2 / 2)."""
    _install(codex_home, BASIC_SID, BASIC)
    act = adapter.parse_activity(BASIC_CWD, BASIC_SID)
    assert (act.human_turns, act.assistant_replies) == (1, 1)


def test_encrypted_reasoning_is_skipped(adapter: CodexAdapter, codex_home: Path) -> None:
    """The opaque ``reasoning`` record (empty summary, encrypted_content) is a
    black box — it must not become a turn entry. Only the assistant message and
    the tool call do."""
    _install(codex_home, BASIC_SID, BASIC)
    (turn,) = adapter.read_turns(BASIC_CWD, BASIC_SID)
    roles = [e.role for e in turn.entries]
    # assistant reply + tool call, no opaque-reasoning entry.
    assert ("assistant", "I'll add the endpoint.") in [(e.role, e.text) for e in turn.entries]
    assert "tool" in roles
    assert all("<opaque>" not in e.text and "gAAAA" not in e.text for e in turn.entries)


def test_read_turns_groups_replies_under_the_prompt(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    _install(codex_home, BASIC_SID, BASIC)
    (turn,) = adapter.read_turns(BASIC_CWD, BASIC_SID)
    assert turn.user_text == "Add a healthcheck endpoint to the service"
    assert [(e.role, e.text) for e in turn.entries] == [
        ("assistant", "I'll add the endpoint."),
        ("tool", "exec_command"),
    ]


def test_status_working_when_a_turn_is_in_flight(adapter: CodexAdapter, codex_home: Path) -> None:
    """A ``task_started`` with no matching ``task_complete`` means a turn is in
    flight → WORKING (the agent's move)."""
    sid = "11111111-1111-7111-8111-111111111111"
    rollout = (
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + sid + '","cwd":"/home/dev/work/flight","model_provider":"openai"}}\n'
        '{"timestamp":"2026-04-28T20:00:01.000Z","type":"event_msg",'
        '"payload":{"type":"task_started","turn_id":"t-open"}}\n'
        '{"timestamp":"2026-04-28T20:00:02.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"do the thing"}]}}\n'
    )
    _install_text(codex_home, sid, rollout)
    assert adapter.parse_activity(Path("/home/dev/work/flight"), sid).state is (
        AgentActivityState.WORKING
    )


def test_status_fallback_to_tail_without_task_events(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """With no task events, status falls back to the response_item tail: a
    trailing assistant message → WAITING; a trailing tool call → WORKING."""
    sid = "22222222-2222-7222-8222-222222222222"
    base = (
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + sid + '","cwd":"/home/dev/work/tail","model_provider":"openai"}}\n'
        '{"timestamp":"2026-04-28T20:00:01.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"go"}]}}\n'
    )
    assistant_tail = (
        '{"timestamp":"2026-04-28T20:00:02.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"assistant",'
        '"content":[{"type":"output_text","text":"done"}]}}\n'
    )
    _install_text(codex_home, sid, base + assistant_tail)
    assert adapter.parse_activity(Path("/home/dev/work/tail"), sid).state is (
        AgentActivityState.WAITING
    )

    tool_tail = (
        '{"timestamp":"2026-04-28T20:00:03.000Z","type":"response_item",'
        '"payload":{"type":"function_call","name":"exec_command","arguments":"{}",'
        '"call_id":"c1"}}\n'
    )
    _install_text(codex_home, sid, base + assistant_tail + tool_tail)
    assert adapter.parse_activity(Path("/home/dev/work/tail"), sid).state is (
        AgentActivityState.WORKING
    )


def test_token_count_uses_latest_cumulative_total(adapter: CodexAdapter, codex_home: Path) -> None:
    """``token_count.info.total_token_usage`` is cumulative — the parser takes
    the LAST populated report, never sums (summing cumulative totals multiplies).
    Two reports: the second's totals win."""
    sid = "33333333-3333-7333-8333-333333333333"
    rollout = (
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + sid + '","cwd":"/home/dev/work/tok","model_provider":"openai"}}\n'
        '{"timestamp":"2026-04-28T20:00:01.000Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"total_token_usage":'
        '{"input_tokens":100,"output_tokens":10}}}}\n'
        '{"timestamp":"2026-04-28T20:00:02.000Z","type":"event_msg",'
        '"payload":{"type":"token_count","info":{"total_token_usage":'
        '{"input_tokens":500,"output_tokens":60}}}}\n'
    )
    _install_text(codex_home, sid, rollout)
    act = adapter.parse_activity(Path("/home/dev/work/tok"), sid)
    assert act.tokens_in == 500  # latest cumulative, NOT 100+500
    assert act.tokens_out == 60


def test_malformed_line_is_skipped_and_never_raises(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """A truncated/garbage line is skipped; the surrounding records still parse."""
    sid = "44444444-4444-7444-8444-444444444444"
    rollout = (
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + sid + '","cwd":"/home/dev/work/bad","model_provider":"openai"}}\n'
        "{this is not valid json\n"
        '{"timestamp":"2026-04-28T20:00:01.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"still parses"}]}}\n'
    )
    _install_text(codex_home, sid, rollout)
    act = adapter.parse_activity(Path("/home/dev/work/bad"), sid)
    assert act.human_turns == 1


def test_missing_session_yields_unknown(adapter: CodexAdapter, codex_home: Path) -> None:
    """No rollout on disk for the id → an empty UNKNOWN activity, never a raise."""
    del codex_home
    act = adapter.parse_activity(Path("/nowhere"), "00000000-0000-7000-8000-000000000000")
    assert act.state is AgentActivityState.UNKNOWN
    assert act.human_turns == 0
    assert act.needs_attention is False


def test_parse_activity_matches_locate_then_parse(adapter: CodexAdapter, codex_home: Path) -> None:
    """``parse_activity(cwd, sid)`` equals the locate→read→parse pipeline."""
    _install(codex_home, BASIC_SID, BASIC)
    located = adapter.locate_transcripts(BASIC_CWD, BASIC_SID)
    legacy = _RolloutParser(CodexAdapter._read(located)).activity()
    assert adapter.parse_activity(BASIC_CWD, BASIC_SID) == legacy


# ─── digest (the #20 seam) ──────────────────────────────────────────────────


def test_digest_skeleton_excludes_tool_output(adapter: CodexAdapter, codex_home: Path) -> None:
    _install(codex_home, BASIC_SID, BASIC)
    digest = adapter.transcript_digest(BASIC_CWD, BASIC_SID)
    roles = [e.role for e in digest.entries]
    assert "user" in roles
    assert "tool" in roles
    assert "exec_command" in [e.text for e in digest.entries if e.role == "tool"]
    # No tool-output payload leaks in.
    assert all("Process exited" not in e.text for e in digest.entries)


# ─── locate / discover (filesystem, hermetic) ───────────────────────────────


def test_locate_finds_rollout_by_session_id(adapter: CodexAdapter, codex_home: Path) -> None:
    _install(codex_home, BASIC_SID, BASIC)
    found = adapter.locate_transcripts(BASIC_CWD, BASIC_SID)
    assert len(found) == 1
    assert found[0].name.endswith(f"{BASIC_SID}.jsonl")


def test_locate_missing_returns_empty(adapter: CodexAdapter, codex_home: Path) -> None:
    del codex_home
    assert adapter.locate_transcripts(Path("/nowhere"), BASIC_SID) == []


def test_discover_by_cwd_reads_session_meta_head(adapter: CodexAdapter, codex_home: Path) -> None:
    """Discovery confirms the cwd from the ``session_meta`` head line (the cwd is
    NOT on every line, unlike Claude). A rollout in a different cwd is excluded."""
    _install(codex_home, BASIC_SID, BASIC)
    assert adapter.discover_sessions(BASIC_CWD) == [BASIC_SID]
    # A different cwd finds nothing.
    assert adapter.discover_sessions(Path("/home/dev/work/other")) == []
    # The Grove-launched id can be excluded (here there are no others left).
    assert adapter.discover_sessions(BASIC_CWD, exclude_id=BASIC_SID) == []


def test_discover_orders_most_recent_first(adapter: CodexAdapter, codex_home: Path) -> None:
    """Discovery returns ids newest-first by rollout mtime, so a workspace with
    no minted id adopts the *live* session (Codex has no settable id, so every
    Codex session arrives this way)."""
    cwd = Path("/home/dev/work/multi")
    older = "0aaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"  # alphabetically first
    newer = "0fffffff-ffff-7fff-8fff-ffffffffffff"  # alphabetically last
    for sid, mtime in ((older, 1000), (newer, 2000)):
        path = _install_text(
            codex_home,
            sid,
            '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
            '"payload":{"id":"' + sid + '","cwd":"' + str(cwd) + '","model_provider":"openai"}}\n',
        )
        os.utime(path, (mtime, mtime))
    assert adapter.discover_sessions(cwd) == [newer, older]


# ─── list_sessions (summaries for the explorer) ─────────────────────────────


def test_list_sessions_builds_summary(adapter: CodexAdapter, codex_home: Path) -> None:
    _install(codex_home, BASIC_SID, BASIC)
    summaries = adapter.list_sessions(BASIC_CWD)
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.session_id == BASIC_SID
    assert summary.adapter_kind == "codex"
    assert summary.cwd == str(BASIC_CWD)
    assert summary.git_branch == "feature/widget"  # from session_meta.git.branch
    assert summary.first_prompt == "Add a healthcheck endpoint to the service"
    assert summary.last_prompt == "Add a healthcheck endpoint to the service"
    assert summary.size_bytes > 0
    assert summary.modified_at is not None
    assert summary.activity.human_turns == 1
    assert summary.activity.state is AgentActivityState.WAITING


def test_list_sessions_empty_when_nothing_recorded(adapter: CodexAdapter, codex_home: Path) -> None:
    del codex_home
    assert adapter.list_sessions(Path("/nowhere")) == []


# ─── registry wiring ────────────────────────────────────────────────────────


def test_get_adapter_selects_codex() -> None:
    assert get_adapter("codex").kind == "codex"
    assert get_adapter("codex").remote is False


def test_codex_launch_decoration_is_empty() -> None:
    """Codex mints its own thread id (no settable id flag), so no decoration —
    Grove correlates purely through fs discovery."""
    assert get_adapter("codex").launch_decoration("anything") == []


def _install_text(codex_home: Path, sid: str, text: str) -> Path:
    target_dir = codex_home / "sessions" / "2026" / "04" / "28"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"rollout-2026-04-28T13-43-44-{sid}.jsonl"
    target.write_text(text, encoding="utf-8")
    return target
