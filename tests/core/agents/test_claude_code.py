"""Pure-unit coverage for the Claude Code adapter + the tool-agnostic model.

Fixtures under ``fixtures/`` are sanitized, hand-built from real on-host
transcript shapes. They pin the parsing invariants that are the crux of the
epic: the real-human-turn filter (the "48 lines, 5 turns" case), resume/fork
de-dup, sidechain + marker exclusion, and survival of a truncated final line.
No network, no tmux, no real ``~/.claude``.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from grove.core.agents import AgentActivityState, get_adapter
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome, _TranscriptParser

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "basic.jsonl"
RESUME = FIXTURES / "basic_resume.jsonl"
NOISE = FIXTURES / "noise.jsonl"

# The session ids and cwds the fixtures record in-line.
BASIC_SID = "11111111-1111-4111-8111-111111111111"
BASIC_CWD = Path("/home/kk/work/svc")
NOISE_SID = "22222222-2222-4222-8222-222222222222"
NOISE_CWD = Path("/home/kk/work/noise")


@pytest.fixture
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandboxed Claude config dir; ``CLAUDE_CONFIG_DIR`` + ``Path.home`` both
    point inside ``tmp_path`` so the adapter never touches the real ``~/.claude``."""
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _install(
    claude_home: Path, cwd: Path, sid: str, source: Path, *, folder: str | None = None
) -> Path:
    """Drop a fixture transcript where ``locate_transcripts(cwd, sid)`` finds it.

    ``folder`` overrides the encoded-cwd directory name, exercising the
    UUID-glob path (the encoding is lossy, so locate never trusts the name).
    """
    target_dir = claude_home / "projects" / (folder or _ClaudeHome.encode_cwd(cwd))
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sid}.jsonl"
    shutil.copyfile(source, target)
    return target


# ─── parsing metrics (through the (cwd, session_id)-keyed surface) ──────────


def test_basic_session_metrics(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    act = adapter.parse_activity(BASIC_CWD, BASIC_SID)

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


def test_resume_dedups_overlapping_records(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """basic + its resume share one assistant line (same id+requestId): count once.

    The two files carry the same session id under different project folders —
    locate's UUID glob returns both (a real resume/fork collision), and the
    parser's content-level de-dup folds the overlap.
    """
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC, folder="folder-a")
    _install(claude_home, BASIC_CWD, BASIC_SID, RESUME, folder="folder-b")
    act = adapter.parse_activity(Path("/elsewhere"), BASIC_SID)

    assert act.human_turns == 3
    assert act.replies_per_turn == (3, 2, 1)
    assert act.assistant_replies == 6
    assert act.tool_calls == 4
    # The duplicated a5 (500 in / 50 out) is not double-counted; only a6 is added.
    assert act.tokens_in == 2100
    assert act.tokens_out == 210
    assert act.state is AgentActivityState.WORKING  # tail is a tool_use
    assert act.needs_attention is False


def test_split_block_lines_merge_into_one_logical_reply(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Claude Code 2.x writes ONE LINE PER CONTENT BLOCK — all lines of one API
    response share ``(message.id, requestId)`` with distinct ``uuid``s (verified
    on-host 2026-06-11). The old first-line-wins de-dup kept only the leading
    ``thinking`` block, silently dropping every text/tool follow-up from turns,
    digests, and tool counts — the "transcripts show no follow-ups" bug.
    """
    sid = "44444444-4444-4444-8444-444444444444"
    cwd = Path("/home/kk/work/split")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    usage = '"usage":{"input_tokens":100,"output_tokens":50}'
    # One response (m1/r1) split across four lines: thinking → text → 2 tool_use.
    lines = [
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"split it"}}',
        f'{{"type":"assistant","uuid":"s1","requestId":"r1","isSidechain":false,'
        f'"timestamp":"2026-06-01T10:00:01.000Z","message":{{"id":"m1","role":"assistant",'
        f'"stop_reason":"tool_use",{usage},'
        f'"content":[{{"type":"thinking","thinking":"hmm"}}]}}}}',
        f'{{"type":"assistant","uuid":"s2","requestId":"r1","isSidechain":false,'
        f'"timestamp":"2026-06-01T10:00:01.100Z","message":{{"id":"m1","role":"assistant",'
        f'"stop_reason":"tool_use",{usage},'
        f'"content":[{{"type":"text","text":"Let me check."}}]}}}}',
        f'{{"type":"assistant","uuid":"s3","requestId":"r1","isSidechain":false,'
        f'"timestamp":"2026-06-01T10:00:01.200Z","message":{{"id":"m1","role":"assistant",'
        f'"stop_reason":"tool_use",{usage},'
        f'"content":[{{"type":"tool_use","name":"Read","input":{{}}}}]}}}}',
        f'{{"type":"assistant","uuid":"s4","requestId":"r1","isSidechain":false,'
        f'"timestamp":"2026-06-01T10:00:01.300Z","message":{{"id":"m1","role":"assistant",'
        f'"stop_reason":"tool_use",{usage},'
        f'"content":[{{"type":"tool_use","name":"Bash","input":{{}}}}]}}}}',
    ]
    (folder / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    act = adapter.parse_activity(cwd, sid)
    assert act.tool_calls == 2  # both tool_use blocks survive the merge
    assert act.tokens_in == 100  # usage identical across siblings: counted ONCE
    assert act.tokens_out == 50
    assert act.assistant_replies == 1  # one logical reply, not four
    assert act.state is AgentActivityState.WORKING  # tool_use tail

    (turn,) = adapter.read_turns(cwd, sid)
    assert [(e.role, e.text) for e in turn.entries] == [
        ("assistant", "Let me check."),
        ("tool", "Read"),
        ("tool", "Bash"),
    ]


def test_trailing_tool_result_reads_working(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """A transcript whose last line is a tool_result is mid-tool — the agent's
    move — even when the previous assistant record closed with ``end_turn``.
    Without the tail advancing on tool_result lines, the status lags a whole
    turn behind reality (a busy session reads WAITING)."""
    sid = "55555555-5555-4555-8555-555555555555"
    cwd = Path("/home/kk/work/midtool")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","name":"Bash","input":{}}]}}\n'
        '{"type":"user","uuid":"t1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user",'
        '"content":[{"type":"tool_result","tool_use_id":"x","content":"ok"}]}}\n',
        encoding="utf-8",
    )
    assert adapter.parse_activity(cwd, sid).state is AgentActivityState.WORKING


def test_noise_excludes_machinery_and_survives_truncation(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Sidechain, slash-command, caveat, and compaction lines are not human turns;
    a truncated final line does not abort the file."""
    _install(claude_home, NOISE_CWD, NOISE_SID, NOISE)
    act = adapter.parse_activity(NOISE_CWD, NOISE_SID)

    assert act.human_turns == 1  # only "Fix the flaky test in the parser"
    assert act.replies_per_turn == (2,)
    assert act.tool_calls == 1
    # Sidechain assistant's 1000/100 tokens are excluded from main-thread totals.
    assert act.tokens_in == 130
    assert act.tokens_out == 13
    assert act.state is AgentActivityState.WAITING


def test_string_boolean_sidechain_is_coerced(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """Defensive: a sidechain flag serialized as the string ``"true"`` must still
    exclude the line (a naive ``bool("true")`` would too, but ``bool("false")``
    would wrongly include — this pins the coercion)."""
    cwd = Path("/home/kk/work/strbool")
    sid = "66666666-6666-4666-8666-666666666666"
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"user","uuid":"x","timestamp":"2026-06-03T10:00:00.000Z",'
        '"isSidechain":"false","message":{"role":"user","content":"real turn"}}\n'
        '{"type":"user","uuid":"y","timestamp":"2026-06-03T10:00:01.000Z",'
        '"isSidechain":"true","message":{"role":"user","content":"sidechain turn"}}\n',
        encoding="utf-8",
    )
    act = adapter.parse_activity(cwd, sid)
    assert act.human_turns == 1  # the "true"-string sidechain line is excluded


def _write_lines(claude_home: Path, cwd: Path, sid: str, lines: list[str]) -> None:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_task_notification_is_a_notification_not_a_human_turn(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A delivered ``<task-notification>`` is a ``type:"user"`` line (verified
    on-host, Claude Code 2.1.x). Without classification it passed the
    human-turn filter and rendered as a raw-XML "user prompt" in every client.
    It must surface as a ``notification`` entry (summary + result extracted),
    advance the tail to WORKING (the agent's move), and close the spawning
    tool_use id so the in-flight sub-agent count drops."""
    sid = "88888888-8888-4888-8888-888888888888"
    cwd = Path("/home/kk/work/notify")
    notice = (
        "<task-notification>\\n<task-id>b8v1e838y</task-id>\\n"
        "<tool-use-id>tu_agent_1</tool-use-id>\\n<status>completed</status>\\n"
        '<summary>Agent \\"Explore webapp\\" completed</summary>\\n'
        "<result>Found the bug in conversation.tsx</result>\\n</task-notification>"
    )
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"map the webapp"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"tool_use","id":"tu_agent_1","name":"Agent",'
            '"input":{"description":"Explore webapp","subagent_type":"Explore","prompt":"go"}}]}}',
            f'{{"type":"user","uuid":"n1","timestamp":"2026-06-01T10:00:05.000Z",'
            f'"isSidechain":false,"message":{{"role":"user","content":"{notice}"}}}}',
        ],
    )

    act = adapter.parse_activity(cwd, sid)
    assert act.human_turns == 1  # the notification is NOT a second human turn
    assert act.state is AgentActivityState.WORKING  # notice delivered → agent's move
    assert act.active_subagents == 0  # the notice closed tu_agent_1

    (turn,) = adapter.read_turns(cwd, sid)
    roles = [(e.role, e.text) for e in turn.entries]
    assert roles[0] == ("tool", "Agent(Explore): Explore webapp")
    assert roles[1][0] == "notification"
    assert 'Agent "Explore webapp" completed' in roles[1][1]
    assert "Found the bug in conversation.tsx" in roles[1][1]
    assert "<task-notification>" not in roles[1][1]  # never raw XML


def test_unresolved_subagent_spawns_count_in_flight(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Three spawns: a foreground Agent (closed by its tool_result), a foreground
    Task (still out), and a backgrounded Agent — whose IMMEDIATE launch-ack
    tool_result must NOT close it (the agent keeps running; only its later
    task-notification is the real return)."""
    sid = "99999999-9999-4999-8999-999999999999"
    cwd = Path("/home/kk/work/fleet")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"tool_use","id":"tu1","name":"Agent",'
            '"input":{"description":"one","subagent_type":"Explore","prompt":"x"}},'
            '{"type":"tool_use","id":"tu2","name":"Task",'
            '"input":{"description":"two","subagent_type":"general-purpose","prompt":"y"}},'
            '{"type":"tool_use","id":"tu3","name":"Agent",'
            '"input":{"description":"bg","subagent_type":"general-purpose","prompt":"z",'
            '"run_in_background":true}}]}}',
            # tu1's real return + tu3's immediate launch-ack arrive together.
            '{"type":"user","uuid":"t1","timestamp":"2026-06-01T10:00:02.000Z",'
            '"isSidechain":false,"message":{"role":"user",'
            '"content":[{"type":"tool_result","tool_use_id":"tu1","content":"done"},'
            '{"type":"tool_result","tool_use_id":"tu3","content":"Async agent launched"}]}}',
        ],
    )
    act = adapter.parse_activity(cwd, sid)
    assert act.active_subagents == 2  # tu2 (no result yet) + tu3 (ack is not a return)
    assert act.state is AgentActivityState.WORKING

    # The background agent's task-notification is what finally closes tu3.
    notice = (
        "<task-notification><tool-use-id>tu3</tool-use-id><status>completed</status>"
        "<summary>done</summary></task-notification>"
    )
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    with (folder / f"{sid}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            '{"type":"user","uuid":"n1","timestamp":"2026-06-01T10:00:03.000Z",'
            f'"isSidechain":false,"message":{{"role":"user","content":"{notice}"}}}}\n'
        )
    assert adapter.parse_activity(cwd, sid).active_subagents == 1  # only tu2 remains


def test_unanswered_ask_user_question_reads_blocked(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """An assistant tail holding an unanswered AskUserQuestion is action-required
    (BLOCKED), not WORKING — the one transcript-visible needs-input signal. Once
    the answer's tool_result lands, the tail advances and it reads WORKING again."""
    sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    cwd = Path("/home/kk/work/ask")
    question_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion",'
        '"input":{"questions":[{"question":"Merge or rebase?"}]}}]}}'
    )
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"finish the branch"}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, question_line])
    act = adapter.parse_activity(cwd, sid)
    assert act.state is AgentActivityState.BLOCKED
    assert act.needs_attention is True

    answered = (
        '{"type":"user","uuid":"t1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user",'
        '"content":[{"type":"tool_result","tool_use_id":"q1","content":"Merge"}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, question_line, answered])
    assert adapter.parse_activity(cwd, sid).state is AgentActivityState.WORKING

    (turn,) = adapter.read_turns(cwd, sid)
    assert ("tool", "AskUserQuestion: Merge or rebase?") in [(e.role, e.text) for e in turn.entries]


def test_missing_session_yields_unknown(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """No transcript on disk for the id (the STARTING window, or a vanished
    file) → an empty UNKNOWN activity, never a raise."""
    del claude_home
    act = adapter.parse_activity(Path("/nowhere"), "77777777-7777-4777-8777-777777777777")
    assert act.state is AgentActivityState.UNKNOWN
    assert act.human_turns == 0
    assert act.needs_attention is False


def test_interpreted_status_reserved_but_unset(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """#20 seam: the field exists and defaults to None (no interpreter wired)."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    assert adapter.parse_activity(BASIC_CWD, BASIC_SID).interpreted_status is None


def test_parse_activity_matches_locate_then_parse(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The reshaped ``parse_activity(cwd, sid)`` equals the old two-step
    locate→parse pipeline on the same fixtures — the #35 reshape moved the
    path resolution inside the adapter without changing what gets parsed."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    located = adapter.locate_transcripts(BASIC_CWD, BASIC_SID)
    legacy = _TranscriptParser(ClaudeCodeAdapter._read(located)).activity()
    assert adapter.parse_activity(BASIC_CWD, BASIC_SID) == legacy


# ─── digest (the #20 seam) ──────────────────────────────────────────────────


def test_digest_skeleton_excludes_tool_results(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    digest = adapter.transcript_digest(BASIC_CWD, BASIC_SID)
    roles = [e.role for e in digest.entries]
    assert "user" in roles
    assert "tool" in roles  # tool_use blocks become TOOL(name) entries
    # No entry carries tool_result payloads.
    assert all("tool_result" not in e.text for e in digest.entries)
    tool_entries = [e.text for e in digest.entries if e.role == "tool"]
    assert "Edit" in tool_entries
    assert "Bash" in tool_entries


# ─── locate_transcripts (filesystem, hermetic) ──────────────────────────────


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


def test_read_turns_groups_replies_under_each_prompt(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    turns = adapter.read_turns(BASIC_CWD, BASIC_SID)

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


def test_read_turns_last_window(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    turns = adapter.read_turns(BASIC_CWD, BASIC_SID, last=1)
    assert len(turns) == 1
    assert turns[0].user_text == "Now write a test for it"
    assert adapter.read_turns(BASIC_CWD, BASIC_SID, last=0) == ()


def test_read_turns_leading_continuation_block(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Assistant records before any human turn (resumed/compacted head) collect
    under an empty-prompt turn instead of being dropped."""
    cwd = Path("/home/kk/work/cont")
    sid = "88888888-8888-4888-8888-888888888888"
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"assistant","uuid":"a1","timestamp":"2026-06-03T10:00:00.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant",'
        '"stop_reason":"end_turn","content":[{"type":"text","text":"Picking up."}]}}\n'
        '{"type":"user","uuid":"h1","timestamp":"2026-06-03T10:00:01.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"Continue please"}}\n',
        encoding="utf-8",
    )
    turns = adapter.read_turns(cwd, sid)
    assert len(turns) == 2
    assert turns[0].user_text == ""
    assert turns[0].entries[0].text == "Picking up."
    assert turns[1].user_text == "Continue please"


# ─── registry + generic/mewbo adapters ──────────────────────────────────────


def test_get_adapter_selects_claude_code() -> None:
    assert get_adapter("claude_code").kind == "claude_code"


def test_get_adapter_unknown_falls_back_to_generic() -> None:
    assert get_adapter("nonsense").kind == "generic"


def test_generic_adapter_is_benign() -> None:
    generic = get_adapter("generic")
    assert generic.launch_decoration("uuid") == []
    assert generic.locate_transcripts(Path("/x"), "uuid") == []
    assert generic.parse_activity(Path("/x"), "uuid").state is AgentActivityState.UNKNOWN
    assert generic.list_sessions(Path("/x")) == []
    assert generic.read_turns(Path("/x"), "uuid") == ()


def test_mewbo_adapter_registers_with_noop_local_surfaces() -> None:
    """The registry resolves ``mewbo``; its *local* surfaces are no-ops by
    design (no CLI to decorate, no transcript files, nothing on this host to
    discover). The REST-backed read surface is covered in ``test_mewbo.py``
    with a mock transport — never via the shared registry singleton, whose
    lazy client would do real network I/O."""
    mewbo = get_adapter("mewbo")
    assert mewbo.kind == "mewbo"
    assert mewbo.launch_decoration("uuid") == []
    assert mewbo.locate_transcripts(Path("/x"), "uuid") == []
    assert mewbo.discover_sessions(Path("/x")) == []


def test_claude_launch_decoration() -> None:
    assert get_adapter("claude_code").launch_decoration("abc-123") == ["--session-id", "abc-123"]
