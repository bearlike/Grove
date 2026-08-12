"""Pure-unit coverage for the Claude Code adapter + the tool-agnostic model.

Fixtures under ``fixtures/`` are sanitized, hand-built from real on-host
transcript shapes. They pin the parsing invariants that are the crux of the
epic: the real-human-turn filter (the "48 lines, 5 turns" case), resume/fork
de-dup, sidechain + marker exclusion, and survival of a truncated final line.
No network, no tmux, no real ``~/.claude``.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents import AgentActivityState, TokenUsage, get_adapter
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome, _TranscriptParser

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "basic.jsonl"
RESUME = FIXTURES / "basic_resume.jsonl"
NOISE = FIXTURES / "noise.jsonl"

# The session ids and cwds the fixtures record in-line.
BASIC_SID = "11111111-1111-4111-8111-111111111111"
BASIC_CWD = Path("/home/dev/work/svc")
NOISE_SID = "22222222-2222-4222-8222-222222222222"
NOISE_CWD = Path("/home/dev/work/noise")


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


def test_current_task_skips_a_relayed_teammate_message_for_the_real_prompt(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A relay is the agent's INBOX, never what it is working on.

    Claude Code writes a ``last-prompt`` record for whatever was submitted
    last, including a peer session's relayed ``<teammate-message>`` — so the
    newest-last-prompt arm published that envelope, wrapper and all, as
    ``current_task`` (reproduced on 4 real on-host sessions). The other arm
    (`_first_human_raw`) had always honoured ``is_human_turn``, which already
    classifies the envelope as machine traffic; only this arm asked nothing.
    Nothing is stripped — the record is skipped, and an older HUMAN prompt
    stands.
    """
    cwd = Path("/home/dev/work/relay")
    sid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    relay = (
        "Another Claude session sent a message: "
        '<teammate-message teammate_id=\\"peer\\" summary=\\"done\\">finished</teammate-message>'
    )
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"user","uuid":"h1","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}",'
        '"message":{"role":"user","content":"Rename the widget module"}}\n'
        f'{{"type":"last-prompt","lastPrompt":"Rename the widget module",'
        f'"leafUuid":"h1","sessionId":"{sid}"}}\n'
        f'{{"type":"user","uuid":"n1","timestamp":"2026-06-09T08:05:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}",'
        f'"message":{{"role":"user","content":"{relay}"}}}}\n'
        f'{{"type":"last-prompt","lastPrompt":"{relay}",'
        f'"leafUuid":"n1","sessionId":"{sid}"}}\n',
        encoding="utf-8",
    )

    act = adapter.parse_activity(cwd, sid)

    assert act.current_task == "Rename the widget module"
    # The two readers are one selection: capped and uncapped must never diverge.
    assert adapter.latest_task(cwd, sid) == "Rename the widget module"
    # `last_prompt` answers a DIFFERENT question — what was submitted last —
    # and the relay genuinely was, so that field keeps reporting it.
    assert adapter.list_sessions(cwd)[0].last_prompt is not None
    assert "teammate-message" in adapter.list_sessions(cwd)[0].last_prompt


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
    cwd = Path("/home/dev/work/split")
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
    cwd = Path("/home/dev/work/midtool")
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
    cwd = Path("/home/dev/work/strbool")
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
    cwd = Path("/home/dev/work/notify")
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
    cwd = Path("/home/dev/work/fleet")
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


def test_teammate_spawn_ack_does_not_close_the_spawn(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The CURRENT (verified CC 2.1.209) in-process-teammate flavor of an
    ``Agent`` spawn carries no ``run_in_background`` at all — backgrounding is
    implicit, signaled only by the launch ack's ``toolUseResult.status ==
    "teammate_spawned"``. That ack ("Spawned successfully. ... The agent is
    now running...") must NOT close the spawn — only a later
    ``<teammate-message>`` idle/completion line does (#209)."""
    sid = "50505050-5050-4505-8505-505050505050"
    cwd = Path("/home/dev/work/teammate-spawn")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-07-14T23:25:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"map the seams"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-07-14T23:25:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"tool_use","id":"toolu_01","name":"Agent",'
            '"input":{"description":"Map the seams","subagent_type":"Explore","model":"sonnet",'
            '"name":"seam-mapper","prompt":"go"}}]}}',
            '{"type":"user","uuid":"t1","isSidechain":false,'
            '"timestamp":"2026-07-14T23:25:24.000Z","message":{"role":"user","content":'
            '[{"type":"tool_result","tool_use_id":"toolu_01","content":'
            '"Spawned successfully. The agent is now running and will receive '
            'instructions via mailbox."}]},'
            '"toolUseResult":{"status":"teammate_spawned",'
            '"teammate_id":"seam-mapper@session-2db82672",'
            '"agent_id":"seam-mapper@session-2db82672","agent_type":"Explore",'
            '"model":"sonnet","name":"seam-mapper","color":"blue",'
            '"team_name":"session-2db82672"}}',
        ],
    )
    act = adapter.parse_activity(cwd, sid)
    assert act.active_subagents == 1  # the launch ack never closes it


def test_idle_notification_closes_the_teammate_spawn_and_is_not_a_human_turn(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The completion signal for a named teammate spawn is NOT a
    ``<task-notification>`` — it's a plain ``type:"user"`` STRING-content line
    wrapping ``<teammate-message teammate_id="...">`` around an embedded JSON
    payload (verified CC 2.1.209, #209). It must (a) close the matching spawn
    by NAME (no tool-use id exists to match by), (b) never count as a second
    human turn, and (c) render as a cooked ``notification`` entry — never raw
    XML markup."""
    sid = "51515151-5151-4515-8515-515151515151"
    cwd = Path("/home/dev/work/teammate-idle")
    spawn_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-07-14T23:25:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"toolu_01","name":"Agent",'
        '"input":{"description":"Map the seams","subagent_type":"Explore","model":"sonnet",'
        '"name":"seam-mapper","prompt":"go"}}]}}'
    )
    ack_line = (
        '{"type":"user","uuid":"t1","isSidechain":false,'
        '"timestamp":"2026-07-14T23:25:24.000Z","message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"toolu_01","content":"Spawned successfully."}]},'
        '"toolUseResult":{"status":"teammate_spawned","name":"seam-mapper"}}'
    )
    teammate_msg = (
        "Another Claude session sent a message:\\n"
        '<teammate-message teammate_id=\\"seam-mapper\\" color=\\"blue\\">\\n'
        '{\\"type\\":\\"idle_notification\\",\\"from\\":\\"seam-mapper\\",'
        '\\"timestamp\\":\\"2026-07-14T23:26:36.020Z\\",\\"idleReason\\":\\"available\\"}\\n'
        "</teammate-message>\\n\\nThis came from another Claude session."
    )
    idle_line = (
        f'{{"type":"user","uuid":"tm1","timestamp":"2026-07-14T23:26:36.030Z",'
        f'"isSidechain":false,"message":{{"role":"user","content":"{teammate_msg}"}}}}'
    )
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-07-14T23:25:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"map the seams"}}',
            spawn_line,
            ack_line,
            idle_line,
        ],
    )
    act = adapter.parse_activity(cwd, sid)
    assert act.active_subagents == 0  # the idle line closed it
    assert act.human_turns == 1  # never a second human turn
    assert act.state is AgentActivityState.WORKING  # the notice is the agent's move

    (turn,) = adapter.read_turns(cwd, sid)
    notes = [e for e in turn.entries if e.role == "notification"]
    assert len(notes) == 1
    assert "seam-mapper is now idle (available)" in notes[0].text
    assert "<teammate-message" not in notes[0].text  # never raw markup
    assert "Another Claude session sent a message" not in notes[0].text


def test_interim_teammate_message_keeps_the_spawn_open(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Teammates relay INTERIM messages while still running (progress
    reports, receipt acks — observed live 2026-07-14: an implementer sent
    "received the delta, starting now" mid-task). Only the structured
    idle_notification means "done": an interim relay must NOT close the
    spawn, or the live fleet undercounts and the WAITING→WORKING promotion
    drops out exactly while a teammate is busiest. The relay still renders
    as a cooked notification, never a human turn."""
    sid = "52525252-5252-4525-8525-525252525252"
    cwd = Path("/home/dev/work/teammate-interim")
    spawn_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-07-14T23:25:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"toolu_01","name":"Agent",'
        '"input":{"description":"Implement the fix","subagent_type":"general-purpose",'
        '"model":"sonnet","name":"impl-core","prompt":"go"}}]}}'
    )
    ack_line = (
        '{"type":"user","uuid":"t1","isSidechain":false,'
        '"timestamp":"2026-07-14T23:25:24.000Z","message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"toolu_01","content":"Spawned successfully."}]},'
        '"toolUseResult":{"status":"teammate_spawned","name":"impl-core"}}'
    )
    interim_msg = (
        "Another Claude session sent a message:\\n"
        '<teammate-message teammate_id=\\"impl-core\\" color=\\"purple\\">\\n'
        "Received the scope delta - starting on the third commit now.\\n"
        "</teammate-message>\\n\\nThis came from another Claude session."
    )
    interim_line = (
        f'{{"type":"user","uuid":"tm1","timestamp":"2026-07-14T23:26:00.000Z",'
        f'"isSidechain":false,"message":{{"role":"user","content":"{interim_msg}"}}}}'
    )
    closing_reply = (
        '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
        '"timestamp":"2026-07-14T23:26:02.000Z","message":{"id":"m2","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"text","text":"Noted - waiting for it to finish."}]}}'
    )
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-07-14T23:25:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"implement the fix"}}',
            spawn_line,
            ack_line,
            interim_line,
            closing_reply,
        ],
    )
    act = adapter.parse_activity(cwd, sid)
    assert act.active_subagents == 1  # interim relay did NOT close the spawn
    assert act.human_turns == 1  # never a second human turn
    # end_turn tail + still-active fleet → the promotion holds WORKING.
    assert act.state is AgentActivityState.WORKING

    (turn,) = adapter.read_turns(cwd, sid)
    notes = [e for e in turn.entries if e.role == "notification"]
    assert len(notes) == 1
    assert "<teammate-message" not in notes[0].text  # never raw markup


def test_workflow_async_launched_ack_stays_open_until_task_output_polls_it(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A Workflow ("ultracode") run's launch ack
    (``toolUseResult.status == "async_launched"``, carrying its OWN async
    ``taskId`` — a DIFFERENT id space than the Task-board taskId
    TaskCreate/TaskUpdate use) must not close the spawn either — it closes
    only once a later ``TaskOutput`` poll for that SAME taskId returns its own
    result (#209). With no such poll, it honestly stays open — the blend's
    staleness demotion is the fallback, never an invented poll."""
    sid = "52525252-5252-4525-8525-525252525252"
    cwd = Path("/home/dev/work/workflow-async")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-07-14T21:50:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"run the workflow"}}'
    )
    spawn_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-07-14T21:50:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"toolu_wf",'
        '"name":"Workflow","input":{"workflowName":"seam-mapper","script":"..."}}]}}'
    )
    ack_line = (
        '{"type":"user","uuid":"t1","isSidechain":false,'
        '"timestamp":"2026-07-14T21:50:03.000Z","message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"toolu_wf","content":"Workflow launched"}]},'
        '"toolUseResult":{"status":"async_launched","taskId":"wvew06yhw",'
        '"taskType":"local_workflow","workflowName":"seam-mapper","runId":"wf_48396aa1-537"}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, spawn_line, ack_line])
    act = adapter.parse_activity(cwd, sid)
    assert act.active_subagents == 1  # the async_launched ack never closes it

    poll_call = (
        '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
        '"timestamp":"2026-07-14T21:58:00.000Z","message":{"id":"m2","role":"assistant",'
        '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"toolu_poll",'
        '"name":"TaskOutput","input":{"taskId":"wvew06yhw"}}]}}'
    )
    poll_result = (
        '{"type":"user","uuid":"t2","isSidechain":false,'
        '"timestamp":"2026-07-14T21:58:05.000Z","message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"toolu_poll","content":"16 agents, 0 errors"}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, spawn_line, ack_line, poll_call, poll_result])
    assert adapter.parse_activity(cwd, sid).active_subagents == 0  # closed by the matching poll


def _write_workflow_worker(
    claude_home: Path, cwd: Path, sid: str, run_id: str, agent_hash: str, lines: list[str]
) -> Path:
    """A Workflow worker transcript at the real recursive on-host layout
    (#209, verified CC 2.1.209): ``<sid>/subagents/workflows/wf_<runId>/
    agent-<hash>.jsonl`` + a SPARSE sibling ``.meta.json`` (no name/color/
    teammate/model fields — just ``agentType``/``spawnDepth``)."""
    sub_dir = (
        claude_home
        / "projects"
        / _ClaudeHome.encode_cwd(cwd)
        / sid
        / "subagents"
        / "workflows"
        / f"wf_{run_id}"
    )
    sub_dir.mkdir(parents=True, exist_ok=True)
    path = sub_dir / f"agent-{agent_hash}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (sub_dir / f"agent-{agent_hash}.meta.json").write_text(
        '{"agentType":"workflow-subagent","spawnDepth":1}', encoding="utf-8"
    )
    return path


def test_fleet_activity_finds_workflow_workers_via_the_recursive_glob(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A Workflow worker nests one level deeper than a plain sub-agent
    (``subagents/workflows/wf_<runId>/agent-<hash>.jsonl``) — the glob behind
    :meth:`locate_transcripts`/:meth:`fleet_activity` must find it recursively
    (#209), and its sparse meta (``{"agentType":"workflow-subagent",
    "spawnDepth":1}`` — no ``name``) degrades identity honestly: no ``name``
    to prefer, so ``title`` falls back to ``agentType``."""
    sid = "53535353-5353-4535-8535-535353535353"
    cwd = Path("/home/dev/work/workflow-worker")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-07-14T21:50:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"run the workflow"}}',
        ],
    )
    _write_workflow_worker(
        claude_home,
        cwd,
        sid,
        "48396aa1-537",
        "a1863ee8cb38de03b",
        [
            '{"type":"user","uuid":"wu1","isSidechain":true,'
            '"agentId":"a1863ee8cb38de03b","timestamp":"2026-07-14T21:50:10.000Z",'
            '"message":{"role":"user","content":"replace the seam"}}',
            '{"type":"assistant","uuid":"wa1","isSidechain":true,'
            '"agentId":"a1863ee8cb38de03b","timestamp":"2026-07-14T21:50:12.000Z",'
            '"message":{"id":"wm1","role":"assistant","stop_reason":"end_turn",'
            '"content":[{"type":"text","text":"Replaced."}]}}',
        ],
    )

    transcripts = adapter.locate_transcripts(cwd, sid)
    assert any(
        "workflows" in p.parts and p.name == "agent-a1863ee8cb38de03b.jsonl" for p in transcripts
    )

    fleet = adapter.fleet_activity(cwd, sid)
    assert len(fleet) == 1
    session, act = fleet[0]
    assert session.session_id == "a1863ee8cb38de03b"
    assert session.parent_session_id == sid
    assert act.title == "workflow-subagent"  # no `name` in the sparse meta → agentType fallback
    assert act.current_task == "replace the seam"  # description absent too → first-prompt fallback


def test_activity_promotes_waiting_to_working_while_background_fleet_runs(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The orchestrator bug: a BACKGROUNDED Agent spawn can close the
    orchestrator's OWN turn (tail ``stop_reason == end_turn`` — the raw
    tail-state rule's WAITING) while the fleet it kicked off keeps running. A
    session whose fleet is active IS working, so the tail-derived WAITING must
    be promoted to WORKING. Once the fleet actually closes (its
    task-notification delivered, and the orchestrator produces its own
    end-of-turn wrap-up), the tail-derived WAITING stands untouched — the
    promotion never fires once the fleet has genuinely gone idle."""
    sid = "40404040-4040-4404-8404-404040404040"
    cwd = Path("/home/dev/work/fleet-promote")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-07-14T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"find the flaky test"}}'
    )
    spawn = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-07-14T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tu1","name":"Agent",'
        '"input":{"description":"Explore the failing suite","subagent_type":"Explore",'
        '"prompt":"go","run_in_background":true}}]}}'
    )
    launch_ack = (
        '{"type":"user","uuid":"t1","timestamp":"2026-07-14T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user",'
        '"content":[{"type":"tool_result","tool_use_id":"tu1","content":"Async agent launched"}]}}'
    )
    wrap_up = (
        '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
        '"timestamp":"2026-07-14T10:00:03.000Z","message":{"id":"m2","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"text",'
        '"text":"Kicked off a background exploration; I will check back."}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, spawn, launch_ack, wrap_up])
    # The real on-host shape: the spawn's own sidechain file exists alongside
    # the main thread while it is still running (unused by the assertions
    # below — active_subagents is derived purely from the main thread — but
    # pinning it here matches what's actually on disk for this scenario).
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "sub01",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"sub01",'
            '"timestamp":"2026-07-14T10:00:02.500Z",'
            '"message":{"role":"user","content":"Find the flaky test, report back."}}',
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"sub01",'
            '"timestamp":"2026-07-14T10:00:04.000Z","message":{"id":"sm1","role":"assistant",'
            '"stop_reason":"tool_use",'
            '"content":[{"type":"tool_use","id":"stu1","name":"Grep","input":{"pattern":"flaky"}}]}}',
        ],
        meta={"agentType": "Explore", "description": "Explore the failing suite"},
    )

    act = adapter.parse_activity(cwd, sid)
    # Raw tail_state (end_turn) would read WAITING; the active fleet promotes it.
    assert act.state is AgentActivityState.WORKING
    assert act.active_subagents == 1

    # The background task finishes: its notification is delivered and the
    # orchestrator produces one more wrap-up reply, closing its own turn.
    notice = (
        "<task-notification><tool-use-id>tu1</tool-use-id><status>completed</status>"
        "<summary>Found it in test_flaky.py</summary></task-notification>"
    )
    closing = (
        '{"type":"assistant","uuid":"a3","requestId":"r3","isSidechain":false,'
        '"timestamp":"2026-07-14T10:00:06.000Z","message":{"id":"m3","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"text","text":"Found the flaky test — it races on setup."}]}}'
    )
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            user_line,
            spawn,
            launch_ack,
            wrap_up,
            f'{{"type":"user","uuid":"n1","timestamp":"2026-07-14T10:00:05.000Z",'
            f'"isSidechain":false,"message":{{"role":"user","content":"{notice}"}}}}',
            closing,
        ],
    )
    act2 = adapter.parse_activity(cwd, sid)
    assert act2.active_subagents == 0  # the notification closed the fleet
    assert act2.state is AgentActivityState.WAITING  # closed fleet: no promotion


def test_activity_blocked_outranks_fleet_promotion(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """An unanswered AskUserQuestion at the tail stays BLOCKED even with an
    active background fleet — action-required outranks "the fleet is still
    working" (only a tail-derived WAITING is ever promoted, never BLOCKED)."""
    sid = "41414141-4141-4141-8141-414141414141"
    cwd = Path("/home/dev/work/fleet-blocked")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-07-14T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out and ask me"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-07-14T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"tool_use","id":"tu1","name":"Agent",'
            '"input":{"description":"probe","subagent_type":"Explore","prompt":"go",'
            '"run_in_background":true}}]}}',
            '{"type":"user","uuid":"t1","timestamp":"2026-07-14T10:00:02.000Z",'
            '"isSidechain":false,"message":{"role":"user",'
            '"content":[{"type":"tool_result","tool_use_id":"tu1",'
            '"content":"Async agent launched"}]}}',
            '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
            '"timestamp":"2026-07-14T10:00:03.000Z","message":{"id":"m2","role":"assistant",'
            '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion",'
            '"input":{"questions":[{"question":"Merge or rebase while it runs?"}]}}]}}',
        ],
    )
    act = adapter.parse_activity(cwd, sid)
    assert act.active_subagents == 1  # tu1 still out
    assert act.state is AgentActivityState.BLOCKED  # never promoted over BLOCKED


def test_unanswered_ask_user_question_reads_blocked(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """An assistant tail holding an unanswered AskUserQuestion is action-required
    (BLOCKED), not WORKING — the one transcript-visible needs-input signal. Once
    the answer's tool_result lands, the tail advances and it reads WORKING again."""
    sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    cwd = Path("/home/dev/work/ask")
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
    # The question now renders as a structured ``question`` entry, answered by
    # the tool_result that followed (the same result that advanced the tail).
    questions = [e for e in turn.entries if e.role == "question"]
    assert len(questions) == 1
    assert questions[0].text == "Merge or rebase?"
    assert questions[0].question is not None
    assert questions[0].question.answered is True
    assert questions[0].question.answer == "Merge"


# ─── structured AgentQuestion entries in turns (epic #74) ───────────────────


def test_ask_user_question_batch_emits_question_entries(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A batched AskUserQuestion (N questions in one tool_use) renders N
    ``role="question"`` rows — one per question — carrying the normalized
    structured payload (kind/options/header/multiselect), sharing the call's
    ``group_id`` and addressed ``{id}#0`` / ``{id}#1``. Unanswered until a
    matching tool_result lands."""
    sid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    cwd = Path("/home/dev/work/ask-batch")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"set it up"}}'
    )
    question_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion",'
        '"input":{"questions":['
        '{"question":"Merge or rebase?","header":"Strategy","multiSelect":false,'
        '"options":[{"label":"Merge","description":"keep both histories"},'
        '{"label":"Rebase","description":"linear history"}]},'
        '{"question":"Which checks?","header":"CI","multiSelect":true,'
        '"options":[{"label":"lint"},{"label":"tests"}]}'
        "]}}]}}"
    )
    _write_lines(claude_home, cwd, sid, [user_line, question_line])

    (turn,) = adapter.read_turns(cwd, sid)
    questions = [e for e in turn.entries if e.role == "question"]
    assert len(questions) == 2

    first, second = questions
    assert first.text == "Merge or rebase?"
    assert first.question is not None
    assert first.question.id == "q1#0"
    assert first.question.group_id == "q1"
    assert first.question.kind == "single_select"
    assert first.question.header == "Strategy"
    assert first.question.multiselect is False
    assert first.question.answered is False
    assert [(o.label, o.description) for o in first.question.options] == [
        ("Merge", "keep both histories"),
        ("Rebase", "linear history"),
    ]

    assert second.question is not None
    assert second.question.id == "q1#1"
    assert second.question.group_id == "q1"  # same batch
    assert second.question.kind == "multi_select"
    assert second.question.header == "CI"
    assert second.question.multiselect is True
    assert second.question.answered is False


def test_question_entries_resolve_once_answered(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Once a tool_result with the matching tool_use_id arrives, every question
    in that batch renders ``answered=True`` with the result content as the
    ``answer`` (group-level resolution, no per-question split)."""
    sid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    cwd = Path("/home/dev/work/ask-answered")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"set it up"}}'
    )
    question_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion",'
        '"input":{"questions":[{"question":"Merge or rebase?"}]}}]}}'
    )
    answered = (
        '{"type":"user","uuid":"t1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user",'
        '"content":[{"type":"tool_result","tool_use_id":"q1","content":"Merge"}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, question_line, answered])

    questions = [
        e for turn in adapter.read_turns(cwd, sid) for e in turn.entries if e.role == "question"
    ]
    assert len(questions) == 1
    assert questions[0].question is not None
    assert questions[0].question.answered is True
    assert questions[0].question.answer == "Merge"


def test_exit_plan_mode_renders_one_confirm_question(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """ExitPlanMode is a plan-approval gate → a single ``role="question"``
    entry, ``kind="confirm"``, prompt == the plan text."""
    sid = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    cwd = Path("/home/dev/work/plan")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"plan it"}}'
    )
    plan_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"p1","name":"ExitPlanMode",'
        '"input":{"plan":"Step 1: refactor. Step 2: ship."}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, plan_line])

    (turn,) = adapter.read_turns(cwd, sid)
    questions = [e for e in turn.entries if e.role == "question"]
    assert len(questions) == 1
    assert questions[0].text == "Step 1: refactor. Step 2: ship."
    assert questions[0].question is not None
    assert questions[0].question.kind == "confirm"
    assert questions[0].question.prompt == "Step 1: refactor. Step 2: ship."


def test_regular_tool_still_renders_as_tool(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """A non-question tool_use (Bash) keeps the existing ``role="tool"`` entry —
    only the question tools route to the structured question entry."""
    sid = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    cwd = Path("/home/dev/work/regular")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"run it"}}'
    )
    bash_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"b1","name":"Bash","input":{}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, bash_line])

    (turn,) = adapter.read_turns(cwd, sid)
    assert [(e.role, e.text) for e in turn.entries] == [("tool", "Bash")]
    assert all(e.role != "question" for e in turn.entries)


# ─── structured FileEdit entries in turns (diff viewer) ─────────────────────


def test_edit_tool_emits_one_file_edit_entry(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """An ``Edit`` tool_use (direct ``old_string``/``new_string`` fields, the
    real Claude Code 2.1.x shape) renders one ``role="file_edit"`` entry
    carrying the structured :class:`FileEdit`, with the one-liner ``text`` set
    to ``"<name> <path>"`` for a role-unaware consumer."""
    sid = "f0000000-0000-4000-8000-000000000001"
    cwd = Path("/home/dev/work/edit")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"tweak it"}}'
    )
    edit_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"e1","name":"Edit",'
        '"input":{"file_path":"/repo/app.py","old_string":"foo","new_string":"bar",'
        '"replace_all":false}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, edit_line])

    (turn,) = adapter.read_turns(cwd, sid)
    edits = [e for e in turn.entries if e.role == "file_edit"]
    assert len(edits) == 1
    assert edits[0].text == "Edit /repo/app.py"
    assert edits[0].file_edit is not None
    assert edits[0].file_edit.path == "/repo/app.py"
    assert edits[0].file_edit.old_text == "foo"
    assert edits[0].file_edit.new_text == "bar"


def test_multi_edit_emits_one_file_edit_entry_per_edit(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A ``MultiEdit`` batch (``edits: [...]`` on one file) renders one
    ``role="file_edit"`` row per edit, in order — the file-edit analogue of a
    batched ``AskUserQuestion`` yielding N question rows."""
    sid = "f0000000-0000-4000-8000-000000000002"
    cwd = Path("/home/dev/work/multiedit")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"batch it"}}'
    )
    multi_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"m1","name":"MultiEdit",'
        '"input":{"file_path":"/repo/app.py","edits":['
        '{"old_string":"foo","new_string":"bar"},'
        '{"old_string":"baz","new_string":"qux"}]}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, multi_line])

    (turn,) = adapter.read_turns(cwd, sid)
    edits = [e for e in turn.entries if e.role == "file_edit"]
    assert len(edits) == 2
    assert all(e.text == "MultiEdit /repo/app.py" for e in edits)
    assert [(e.file_edit.old_text, e.file_edit.new_text) for e in edits if e.file_edit] == [
        ("foo", "bar"),
        ("baz", "qux"),
    ]


def test_write_tool_emits_file_edit_with_empty_old_text(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A ``Write`` (``content`` only, no ``old_string``) renders one
    ``file_edit`` whose ``old_text`` is empty — an honest all-additions diff,
    never backfilled from disk."""
    sid = "f0000000-0000-4000-8000-000000000003"
    cwd = Path("/home/dev/work/write")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"make it"}}'
    )
    write_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"w1","name":"Write",'
        '"input":{"file_path":"/repo/new.py","content":"print(1)"}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, write_line])

    (turn,) = adapter.read_turns(cwd, sid)
    edits = [e for e in turn.entries if e.role == "file_edit"]
    assert len(edits) == 1
    assert edits[0].text == "Write /repo/new.py"
    assert edits[0].file_edit is not None
    assert edits[0].file_edit.old_text == ""
    assert edits[0].file_edit.new_text == "print(1)"


def test_non_edit_tool_unaffected_by_file_edit_branch(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A read-only tool (``Read``) still renders ``role="tool"`` — the file-edit
    branch only fires for the edit tools, never a bystander tool_use."""
    sid = "f0000000-0000-4000-8000-000000000004"
    cwd = Path("/home/dev/work/read")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"look at it"}}'
    )
    read_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"r1","name":"Read",'
        '"input":{"file_path":"/repo/app.py"}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, read_line])

    (turn,) = adapter.read_turns(cwd, sid)
    assert [(e.role, e.text) for e in turn.entries] == [("tool", "Read")]
    assert all(e.role != "file_edit" for e in turn.entries)


def test_question_tool_unaffected_by_file_edit_branch(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A question tool still routes to ``role="question"`` — the file-edit branch
    sits right next to the question branch, so guard that it didn't steal it."""
    sid = "f0000000-0000-4000-8000-000000000005"
    cwd = Path("/home/dev/work/ask-guard")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"decide"}}'
    )
    question_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"q1","name":"AskUserQuestion",'
        '"input":{"questions":[{"question":"Merge or rebase?"}]}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, question_line])

    (turn,) = adapter.read_turns(cwd, sid)
    assert [e.role for e in turn.entries] == ["question"]
    assert all(e.role != "file_edit" for e in turn.entries)


def test_todowrite_emits_one_todo_entry_carrying_the_list(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A ``TodoWrite`` tool_use (the real Claude Code shape: ``input.todos[]`` with
    ``content``/``status``/``activeForm``) renders ONE ``role="todo"`` entry
    carrying the whole normalized list, with ``text`` set to the progress summary
    for a role-unaware consumer. Before #184 this fell through to a bare
    ``role="tool"`` "TodoWrite" and the list was discarded."""
    sid = "f0000000-0000-4000-8000-000000000006"
    cwd = Path("/home/dev/work/todo")
    user_line = (
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"plan it"}}'
    )
    todo_line = (
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"t1","name":"TodoWrite",'
        '"input":{"todos":['
        '{"content":"Read the code","status":"completed","activeForm":"Reading"},'
        '{"content":"Write the fix","status":"in_progress","activeForm":"Writing"}]}}]}}'
    )
    _write_lines(claude_home, cwd, sid, [user_line, todo_line])

    (turn,) = adapter.read_turns(cwd, sid)
    todos = [e for e in turn.entries if e.role == "todo"]
    assert len(todos) == 1
    assert todos[0].text == "1/2 done · Write the fix"
    assert todos[0].todo is not None
    assert [(i.content, i.status, i.active_form) for i in todos[0].todo.items] == [
        ("Read the code", "completed", "Reading"),
        ("Write the fix", "in_progress", "Writing"),
    ]
    # It must NOT double-render as a generic tool.
    assert all(e.role != "tool" for e in turn.entries)


def test_taskcreate_and_taskupdate_reconstruct_a_todo_board(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Claude Code's Jan-2026 Task system (#188) splits ``TodoWrite`` into
    per-item ``TaskCreate`` + per-change ``TaskUpdate`` calls, each correlated
    by a server-assigned id that rides back only in ``TaskCreate``'s own
    ``tool_result`` (``"Task #<n> created successfully: …"``). Every mutating
    call renders its own ``role="todo"`` entry carrying the BOARD'S CURRENT
    snapshot — the same shape ``TodoWrite`` renders — so the existing pinned
    card needs no changes to pick up either provider shape."""
    sid = "f0000000-0000-4000-8000-000000000007"
    cwd = Path("/home/dev/work/tasks")
    lines = [
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"track the work"}}',
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tc1","name":"TaskCreate",'
        '"input":{"subject":"Fix the bug","activeForm":"Fixing the bug"}}]}}',
        '{"type":"user","uuid":"u2","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tc1",'
        '"content":"Task #1 created successfully: Fix the bug"}]}}',
        '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:03.000Z","message":{"id":"m2","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tc2","name":"TaskCreate",'
        '"input":{"subject":"Write the test"}}]}}',
        '{"type":"user","uuid":"u3","timestamp":"2026-06-01T10:00:04.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tc2",'
        '"content":"Task #2 created successfully: Write the test"}]}}',
        '{"type":"assistant","uuid":"a3","requestId":"r3","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:05.000Z","message":{"id":"m3","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tu1","name":"TaskUpdate",'
        '"input":{"taskId":"1","status":"in_progress"}}]}}',
    ]
    _write_lines(claude_home, cwd, sid, lines)

    (turn,) = adapter.read_turns(cwd, sid)
    todos = [e for e in turn.entries if e.role == "todo"]
    assert len(todos) == 3  # one snapshot per mutating call
    assert todos[0].todo is not None and todos[1].todo is not None and todos[2].todo is not None
    assert [(i.content, i.status, i.active_form) for i in todos[0].todo.items] == [
        ("Fix the bug", "pending", "Fixing the bug"),
    ]
    assert [(i.content, i.status) for i in todos[1].todo.items] == [
        ("Fix the bug", "pending"),
        ("Write the test", "pending"),
    ]
    final = todos[2].todo.items
    assert [(i.content, i.status) for i in final] == [
        ("Fix the bug", "in_progress"),
        ("Write the test", "pending"),
    ]
    # Neither TaskCreate nor TaskUpdate double-renders as a generic tool.
    assert all(e.role != "tool" for e in turn.entries)


def test_taskcreate_with_unresolvable_id_falls_back_to_generic_tool(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A ``TaskCreate`` whose ``tool_result`` never arrived (or didn't match
    the expected confirmation shape) can't be assigned an id — the call falls
    back to the ordinary ``role="tool"`` entry rather than a fabricated or
    dropped task."""
    sid = "f0000000-0000-4000-8000-000000000008"
    cwd = Path("/home/dev/work/tasks-unresolved")
    lines = [
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"track it"}}',
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tc1","name":"TaskCreate",'
        '"input":{"subject":"Orphaned create"}}]}}',
    ]
    _write_lines(claude_home, cwd, sid, lines)

    (turn,) = adapter.read_turns(cwd, sid)
    assert [(e.role, e.text) for e in turn.entries] == [("tool", "TaskCreate")]


def test_taskupdate_for_untracked_id_falls_back_to_generic_tool(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A ``TaskUpdate`` referencing an id this board never resolved a create
    for (its create call may be outside the loaded window) degrades to the
    generic tool entry rather than fabricating a task out of thin air."""
    sid = "f0000000-0000-4000-8000-000000000009"
    cwd = Path("/home/dev/work/tasks-untracked-update")
    lines = [
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"mark it done"}}',
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tu1","name":"TaskUpdate",'
        '"input":{"taskId":"5","status":"completed"}}]}}',
    ]
    _write_lines(claude_home, cwd, sid, lines)

    (turn,) = adapter.read_turns(cwd, sid)
    assert [(e.role, e.text) for e in turn.entries] == [("tool", "TaskUpdate")]


def test_tasklist_and_taskget_render_as_generic_tool_calls(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """``TaskList``/``TaskGet`` are read-only queries of the same board and
    surface no new state — deliberately unrecognized (see ``TASK_TOOL_NAMES``),
    so they render like any other bystander tool call, never touching the
    board or absorbing into a todo entry."""
    sid = "f0000000-0000-4000-8000-00000000000a"
    cwd = Path("/home/dev/work/tasks-reads")
    lines = [
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"what is left?"}}',
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tl1","name":"TaskList","input":{}}]}}',
        '{"type":"user","uuid":"u2","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tl1","content":"[]"}]}}',
        '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:03.000Z","message":{"id":"m2","role":"assistant",'
        '"stop_reason":"end_turn","usage":{"input_tokens":1,"output_tokens":1},'
        '"content":[{"type":"tool_use","id":"tg1","name":"TaskGet",'
        '"input":{"taskId":"1"}}]}}',
    ]
    _write_lines(claude_home, cwd, sid, lines)

    (turn,) = adapter.read_turns(cwd, sid)
    assert [(e.role, e.text) for e in turn.entries] == [
        ("tool", "TaskList"),
        ("tool", "TaskGet"),
    ]
    assert all(e.role != "todo" for e in turn.entries)


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
    cwd = Path("/home/dev/work/svc")
    sid = "33333333-3333-4333-8333-333333333333"
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    main = folder / f"{sid}.jsonl"
    main.write_text('{"type":"user","cwd":"/home/dev/work/svc"}\n', encoding="utf-8")
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
    cwd = Path("/home/dev/work/multi")
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
    cwd = Path("/home/dev/work/preamble")
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


def test_discover_births_reads_birth_from_head(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """#F5: ``discover_births`` pairs each id with its BIRTH (first timestamped
    record) from the SAME bounded head read that confirms the cwd — no full parse
    — so the adoption gate rejects history cheaply. Ordered newest-first by mtime
    like ``discover_sessions``, and the cwdless/timestampless preamble is skipped."""
    cwd = Path("/home/dev/work/births")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    older = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    newer = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    births = {older: "2026-06-01T10:00:00.000Z", newer: "2026-06-02T11:30:00.000Z"}
    for sid, mtime in ((older, 1000), (newer, 2000)):
        path = folder / f"{sid}.jsonl"
        path.write_text(
            '{"type":"mode","mode":"default"}\n'  # preamble: no cwd, no timestamp
            f'{{"type":"user","cwd":"{cwd}","timestamp":"{births[sid]}",'
            '"message":{"role":"user","content":"hi"}}\n',
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    result = adapter.discover_births(cwd)
    assert [sid for sid, _birth, _mtime in result] == [newer, older]  # newest-first by mtime
    by_id = {sid: birth for sid, birth, _mtime in result}
    assert by_id[newer] == datetime(2026, 6, 2, 11, 30, tzinfo=UTC)
    assert by_id[older] == datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


# ─── discover_all (the host-wide catalog scan, epic: Session Catalog) ──────


def test_discover_all_walks_every_folder_across_the_cascade(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The catalog scan is deliberately broader than ``discover_paths``: it
    must find sessions in EVERY encoded-cwd folder, not just one. Each ref
    carries the cwd/branch read from the same head-read record, newest-first
    by mtime."""
    project_a = Path("/home/dev/work/alpha")
    project_b = Path("/home/dev/work/beta")
    folder_a = claude_home / "projects" / _ClaudeHome.encode_cwd(project_a)
    folder_b = claude_home / "projects" / _ClaudeHome.encode_cwd(project_b)
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)
    sid_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    sid_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    path_a = folder_a / f"{sid_a}.jsonl"
    path_b = folder_b / f"{sid_b}.jsonl"
    path_a.write_text(
        f'{{"type":"user","cwd":"{project_a}","gitBranch":"main",'
        '"timestamp":"2026-06-01T10:00:00.000Z"}\n',
        encoding="utf-8",
    )
    path_b.write_text(
        f'{{"type":"user","cwd":"{project_b}","gitBranch":"feature/x",'
        '"timestamp":"2026-06-02T10:00:00.000Z"}\n',
        encoding="utf-8",
    )
    os.utime(path_a, (1000, 1000))
    os.utime(path_b, (2000, 2000))

    refs = adapter.discover_all()
    assert [ref.session_id for ref in refs] == [sid_b, sid_a]  # newest-first by mtime
    by_id = {ref.session_id: ref for ref in refs}
    assert by_id[sid_a].cwd == str(project_a)
    assert by_id[sid_a].git_branch == "main"
    assert by_id[sid_a].adapter_kind == "claude_code"
    # size_bytes rides the same stat() call as mtime — zero extra I/O.
    assert by_id[sid_a].size_bytes == path_a.stat().st_size
    assert by_id[sid_b].cwd == str(project_b)
    assert by_id[sid_b].git_branch == "feature/x"
    assert by_id[sid_b].size_bytes == path_b.stat().st_size


def test_discover_all_degrades_a_cwdless_transcript_instead_of_dropping_it(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """~2 % of real transcripts never reveal a cwd in a bounded head read
    (#226 evidence). The row still surfaces with ``cwd=None`` — it is never
    silently dropped from the catalog."""
    folder = claude_home / "projects" / "some-unresolvable-folder"
    folder.mkdir(parents=True)
    sid = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"mode","mode":"default"}\n{"type":"summary","summary":"no cwd here"}\n',
        encoding="utf-8",
    )

    refs = adapter.discover_all()
    assert len(refs) == 1
    assert refs[0].session_id == sid
    assert refs[0].cwd is None
    assert refs[0].git_branch is None


def test_discover_paths_never_walks_the_projects_root(
    adapter: ClaudeCodeAdapter, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2 s activity poll's cost must not change (#227): per-cwd discovery
    must reach its answer through the one forward-encoded folder, never by
    iterating every folder under ``projects/`` — that broader walk belongs to
    ``discover_all`` alone. Proven by making a host-wide ``iterdir`` a hard
    error; ``discover_paths``/``discover_sessions`` never call it (only the
    direct encoded-folder ``glob``, which pathlib implements without
    ``Path.iterdir``)."""
    cwd = Path("/home/dev/work/one-folder")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True)
    sid = "66666666-6666-4666-8666-666666666666"
    (folder / f"{sid}.jsonl").write_text(f'{{"type":"user","cwd":"{cwd}"}}\n', encoding="utf-8")
    (claude_home / "projects" / "some-other-encoded-folder").mkdir(parents=True)

    real_iterdir = Path.iterdir

    def _guarded_iterdir(self: Path) -> Iterator[Path]:
        if self == claude_home / "projects":
            raise AssertionError("discover_paths must not walk the projects root")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", _guarded_iterdir)
    assert adapter.discover_sessions(cwd) == [sid]


# ─── encode_cwd (the documented folder rule) ────────────────────────────────


def test_encode_cwd_replaces_every_non_alphanumeric() -> None:
    """The Agent SDK documents the encoding as *every* non-alphanumeric char →
    ``-`` — not just ``/`` ``.`` ``_``. A cwd with ``@``/``+``/space must still
    hit the fast path."""
    assert _ClaudeHome.encode_cwd(Path("/home/dev/my proj+v2@x")) == "-home-dev-my-proj-v2-x"
    assert _ClaudeHome.encode_cwd(Path("/home/dev/.claude_dir")) == "-home-dev--claude-dir"


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
    cwd = Path("/home/dev/work/listing")
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
    cwd = Path("/home/dev/work/cont")
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
    assert generic.final_result(Path("/x"), "uuid") is None
    assert generic.offline_decoration() == []


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
    assert mewbo.offline_decoration() == []


def test_claude_launch_decoration() -> None:
    assert get_adapter("claude_code").launch_decoration("abc-123") == ["--session-id", "abc-123"]


# ─── the agentic-loop spine (#179) ──────────────────────────────────────────


def test_read_messages_maps_roles_content_ids_and_usage(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The spine is the lineage-preserving parse ``read_turns`` / ``digest``
    project from: one message per logical record, metadata records dropped,
    stable ids and per-message usage carried. Cache fields stay SEPARATE (never
    folded into one number) and ``reasoning`` is ``None`` (Claude reports no
    reasoning-token count) — an absent count is never fabricated."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    messages = adapter.read_messages(BASIC_CWD, BASIC_SID)

    # ai-title / last-prompt are metadata, not loop messages → dropped.
    assert [m.role for m in messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
        "assistant",
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    first_user, first_assistant = messages[0], messages[1]
    assert first_user.text() == "Add a healthcheck endpoint to the service"
    assert first_user.usage is None  # a human turn carries no usage
    assert first_user.message_id is None
    assert first_user.timestamp == datetime.fromisoformat("2026-06-01T10:00:00.000Z")

    assert first_assistant.message_id == "msg-1"
    assert first_assistant.model == "claude-opus-4-8"
    assert [b.type for b in first_assistant.content] == ["text", "tool_use"]
    assert first_assistant.usage == TokenUsage(input=100, output=10)

    # A reported 0 is a real count, distinct from an absent (None) field, and a
    # cache read stays in its own field rather than being folded into `input`.
    assert messages[3].usage == TokenUsage(input=0, output=20, cache_read=200)

    # A tool-result carrier maps to a `tool` message whose block names the call.
    tool_result = messages[2].content[0]
    assert tool_result.type == "tool_result"
    assert tool_result.tool_use_id


def test_read_messages_preserves_subagent_lineage(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A sub-agent transcript is a SEPARATE file (``<sid>/subagents/agent-*.jsonl``)
    whose records carry ``isSidechain`` + ``agentId`` + ``sourceToolAssistantUUID``
    (on-host, Claude Code 2.1.x). The spine keeps those messages with their
    lineage — ``is_sidechain`` / ``thread_id`` (= agentId) / ``parent_tool_use_id``
    (= the spawning assistant uuid) — while the main-thread turn projection
    excludes them (byte-identical: sub-agent threads never were turns)."""
    cwd = Path("/home/dev/work/fleet-spine")
    sid = "12121212-1212-4121-8121-121212121212"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"delegate it"}}',
            '{"type":"assistant","uuid":"main-a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","usage":{"input_tokens":1,"output_tokens":1},'
            '"content":[{"type":"tool_use","id":"tu1","name":"Task",'
            '"input":{"description":"probe","subagent_type":"Explore","prompt":"go"}}]}}',
        ],
    )
    sub_dir = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd) / sid / "subagents"
    sub_dir.mkdir(parents=True)
    (sub_dir / "agent-a99.jsonl").write_text(
        '{"type":"assistant","uuid":"s1","isSidechain":true,"agentId":"a99",'
        '"sourceToolAssistantUUID":"main-a1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"message":{"id":"sm1","role":"assistant","stop_reason":"end_turn",'
        '"content":[{"type":"text","text":"sub-agent working"}]}}\n',
        encoding="utf-8",
    )

    messages = adapter.read_messages(cwd, sid)
    sidechain = [m for m in messages if m.is_sidechain]
    assert len(sidechain) == 1
    assert sidechain[0].role == "assistant"
    assert sidechain[0].thread_id == "a99"
    assert sidechain[0].parent_tool_use_id == "main-a1"
    assert sidechain[0].text() == "sub-agent working"
    # Main-thread messages leave the lineage fields unset.
    assert all(m.thread_id is None and m.parent_tool_use_id is None for m in messages[:2])

    # The turn projection excludes the sub-agent thread — one main-thread turn.
    (turn,) = adapter.read_turns(cwd, sid)
    assert all("sub-agent working" not in e.text for e in turn.entries)


def test_read_messages_notification_carries_spawning_tool_id(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A delivered ``<task-notification>`` maps to a ``notification`` message
    whose ``tool_use_id`` is the spawning tool it reports on (so a fleet view
    closes the right in-flight id), with the cooked summary as its text — never
    raw XML."""
    sid = "13131313-1313-4131-8131-131313131313"
    cwd = Path("/home/dev/work/notify-spine")
    notice = (
        "<task-notification><tool-use-id>tu_bg</tool-use-id><status>completed</status>"
        "<summary>Explore done</summary><result>found it</result></task-notification>"
    )
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}',
            f'{{"type":"user","uuid":"n1","timestamp":"2026-06-01T10:00:05.000Z",'
            f'"isSidechain":false,"message":{{"role":"user","content":"{notice}"}}}}',
        ],
    )
    messages = adapter.read_messages(cwd, sid)
    notifications = [m for m in messages if m.role == "notification"]
    assert len(notifications) == 1
    assert notifications[0].tool_use_id == "tu_bg"
    assert "Explore done" in notifications[0].text()
    assert "<task-notification>" not in notifications[0].text()


def test_read_turns_and_digest_are_projections_of_read_messages(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The public projections read from the SAME spine (one parse, many
    projections): every user prompt in the spine appears verbatim as a turn's
    ``user_text``, and every digest ``user`` line is a truncation of one."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    messages = adapter.read_messages(BASIC_CWD, BASIC_SID)
    spine_prompts = [m.text() for m in messages if m.role == "user"]
    turn_prompts = [t.user_text for t in adapter.read_turns(BASIC_CWD, BASIC_SID)]
    assert turn_prompts == spine_prompts


# ─── typed final-result extraction (#149) ───────────────────────────────────


def test_final_result_complete_on_a_tail_text_only_reply(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The BASIC fixture's tail is an ``end_turn`` assistant reply with no
    tool_use block — the terminal-result shape — so ``final_result`` reports
    it complete with the reply's own text, mirroring the tail's own
    ``stop_reason in (end_turn, stop_sequence)`` without needing that field."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    result = adapter.final_result(BASIC_CWD, BASIC_SID)
    assert result is not None
    assert result.is_complete is True
    assert result.text == adapter.read_messages(BASIC_CWD, BASIC_SID)[-1].text()


def test_final_result_none_before_any_assistant_reply(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """No assistant has spoken yet (a lone human turn) — degrade to ``None``
    rather than a misleading empty result."""
    cwd = Path("/home/dev/work/final-result-none")
    sid = "21212121-2121-4121-8121-212121212121"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}',
        ],
    )
    assert adapter.final_result(cwd, sid) is None


def test_final_result_incomplete_while_a_tool_call_is_open(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A tail assistant message still holding a ``tool_use`` block (the
    ``stop_reason == "tool_use"`` shape) is not a final answer — working or
    blocked-on-a-question either way."""
    cwd = Path("/home/dev/work/final-result-open-tool")
    sid = "22222222-2222-4222-8222-222222222222"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1",'
            '"name":"Bash","input":{"command":"ls"}}]}}',
        ],
    )
    result = adapter.final_result(cwd, sid)
    assert result is not None
    assert result.is_complete is False


def test_final_result_incomplete_when_a_tool_result_trails_the_assistant(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Once the tool returns, the TAIL is the ``tool`` message, not the
    assistant that called it — ``is_complete`` stays ``False`` even though
    that assistant message alone carries no unresolved question, because the
    agent still owes a reply to the tool result."""
    cwd = Path("/home/dev/work/final-result-tool-trails")
    sid = "23232323-2323-4232-8232-232323232323"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1",'
            '"name":"Bash","input":{"command":"ls"}}]}}',
            '{"type":"user","uuid":"u2","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:02.000Z","message":{"role":"user","content":'
            '[{"type":"tool_result","tool_use_id":"tu1","content":"ok"}]}}',
        ],
    )
    result = adapter.final_result(cwd, sid)
    assert result is not None
    assert result.is_complete is False
    # The last ASSISTANT turn's text still surfaces (empty here — a bare tool
    # call carries no prose), never the unrelated tool-result carrier's text.
    assert result.text == ""


def test_final_result_skips_a_trailing_sidechain_message(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A sub-agent reply is not the main thread's final answer even when it is
    the newest record on disk — the projection must look past it to the
    real main-thread tail."""
    cwd = Path("/home/dev/work/final-result-sidechain")
    sid = "24242424-2424-4242-8242-242424242424"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"end_turn","content":[{"type":"text","text":"done"}]}}',
        ],
    )
    sub_dir = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd) / sid / "subagents"
    sub_dir.mkdir(parents=True)
    (sub_dir / "agent-a99.jsonl").write_text(
        '{"type":"assistant","uuid":"s1","isSidechain":true,"agentId":"a99",'
        '"sourceToolAssistantUUID":"a1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"message":{"id":"sm1","role":"assistant","stop_reason":"end_turn",'
        '"content":[{"type":"text","text":"sub-agent reply"}]}}\n',
        encoding="utf-8",
    )
    result = adapter.final_result(cwd, sid)
    assert result is not None
    assert result.is_complete is True
    assert result.text == "done"


# ─── latest-todo projection (#194) ──────────────────────────────────────────


def test_latest_todo_none_before_any_todo_tool_is_called(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A session that never called a todo/Task tool projects to ``None`` —
    never a misleading empty card."""
    cwd = Path("/home/dev/work/latest-todo-none")
    sid = "31313131-3131-4131-8131-313131313131"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"go"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1",'
            '"name":"Bash","input":{"command":"ls"}}]}}',
        ],
    )
    assert adapter.latest_todo(cwd, sid) is None


def test_latest_todo_from_a_todowrite_call(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """A single ``TodoWrite`` call is the whole list — the projection reads
    straight through to it, mirroring the ``final_result_from_messages``
    recipe over the same spine."""
    cwd = Path("/home/dev/work/latest-todo-todowrite")
    sid = "32323232-3232-4232-8232-323232323232"
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"track it"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tw1",'
            '"name":"TodoWrite","input":{"todos":['
            '{"content":"Read the code","status":"completed","activeForm":"Reading"},'
            '{"content":"Write the fix","status":"in_progress","activeForm":"Writing"}'
            "]}}]}}",
        ],
    )
    todo = adapter.latest_todo(cwd, sid)
    assert todo is not None
    assert [(i.content, i.status, i.active_form) for i in todo.items] == [
        ("Read the code", "completed", "Reading"),
        ("Write the fix", "in_progress", "Writing"),
    ]


def test_latest_todo_reflects_the_boards_final_snapshot(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Multiple ``TaskCreate``/``TaskUpdate`` calls fold onto one running
    board (#188) — the projection reports the board's state AFTER the last
    mutating call, not the first one it ever saw."""
    cwd = Path("/home/dev/work/latest-todo-board")
    sid = "33333333-3333-4333-8333-333333333333"
    lines = [
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"track the work"}}',
        '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
        '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tc1","name":"TaskCreate",'
        '"input":{"subject":"Fix the bug","activeForm":"Fixing the bug"}}]}}',
        '{"type":"user","uuid":"u2","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tc1",'
        '"content":"Task #1 created successfully: Fix the bug"}]}}',
        '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:03.000Z","message":{"id":"m2","role":"assistant",'
        '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tc2","name":"TaskCreate",'
        '"input":{"subject":"Write the test"}}]}}',
        '{"type":"user","uuid":"u3","timestamp":"2026-06-01T10:00:04.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tc2",'
        '"content":"Task #2 created successfully: Write the test"}]}}',
        '{"type":"assistant","uuid":"a3","requestId":"r3","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:05.000Z","message":{"id":"m3","role":"assistant",'
        '"stop_reason":"end_turn","content":[{"type":"tool_use","id":"tu1","name":"TaskUpdate",'
        '"input":{"taskId":"1","status":"in_progress"}}]}}',
    ]
    _write_lines(claude_home, cwd, sid, lines)
    todo = adapter.latest_todo(cwd, sid)
    assert todo is not None
    assert [(i.content, i.status) for i in todo.items] == [
        ("Fix the bug", "in_progress"),
        ("Write the test", "pending"),
    ]


def test_latest_todo_folds_a_taskupdate_correlated_many_turns_after_its_taskcreate(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """THE LOAD-BEARING CASE (#194): a ``TaskUpdate`` near the tail references
    an id whose owning ``TaskCreate`` (and the ``tool_result`` carrying its
    server-assigned id, see ``TaskBoard.created_task_id``) sits dozens of
    turns earlier. Correctness requires folding the WHOLE transcript from
    session start — a "last N turns" tail read would prune the original
    ``TaskCreate``/``tool_result`` pair out of its window, so ``TaskUpdate``'s
    ``taskId`` would never resolve against a tracked task and the projection
    would wrongly report ``None`` (or a stale board) instead of the real
    final state.
    """
    cwd = Path("/home/dev/work/latest-todo-many-turns")
    sid = "34343434-3434-4343-8343-343434343434"
    lines = [
        '{"type":"user","uuid":"u0","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"track the work"}}',
        '{"type":"assistant","uuid":"a0","requestId":"r0","isSidechain":false,'
        '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m0","role":"assistant",'
        '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tc1","name":"TaskCreate",'
        '"input":{"subject":"Fix the bug","activeForm":"Fixing the bug"}}]}}',
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tc1",'
        '"content":"Task #1 created successfully: Fix the bug"}]}}',
    ]
    # Dozens of unrelated human/assistant turns between the create and the
    # eventual update — far past any plausible bounded tail window.
    for n in range(2, 42):
        minute = f"{10 + n // 60:02d}:{n % 60:02d}"
        lines.append(
            f'{{"type":"user","uuid":"u{n}","timestamp":"2026-06-01T{minute}:00.000Z",'
            f'"isSidechain":false,"message":{{"role":"user","content":"continue step {n}"}}}}'
        )
        lines.append(
            f'{{"type":"assistant","uuid":"a{n}","requestId":"r{n}","isSidechain":false,'
            f'"timestamp":"2026-06-01T{minute}:01.000Z","message":{{"id":"m{n}",'
            '"role":"assistant","stop_reason":"end_turn",'
            f'"content":[{{"type":"text","text":"working on step {n}"}}]}}}}'
        )
    lines.append(
        '{"type":"assistant","uuid":"aLast","requestId":"rLast","isSidechain":false,'
        '"timestamp":"2026-06-01T10:59:59.000Z","message":{"id":"mLast","role":"assistant",'
        '"stop_reason":"end_turn","content":[{"type":"tool_use","id":"tuLast","name":"TaskUpdate",'
        '"input":{"taskId":"1","status":"completed"}}]}}'
    )
    _write_lines(claude_home, cwd, sid, lines)

    todo = adapter.latest_todo(cwd, sid)

    assert todo is not None
    assert [(i.content, i.status, i.active_form) for i in todo.items] == [
        ("Fix the bug", "completed", "Fixing the bug"),
    ]


# ─── fleet reader (#173) ─────────────────────────────────────────────────────


def _write_subagent(
    claude_home: Path,
    cwd: Path,
    sid: str,
    agent_id: str,
    lines: list[str],
    *,
    meta: dict[str, object] | None = None,
) -> Path:
    """Drop one sub-agent transcript (+ optional sibling ``.meta.json``) where
    the fleet reader finds it: ``<sid>/subagents/agent-{agent_id}.jsonl`` —
    verified on-host layout (real ``~/.claude/projects`` transcripts, 2026-07-08)."""
    sub_dir = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd) / sid / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    path = sub_dir / f"agent-{agent_id}.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if meta is not None:
        (sub_dir / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return path


def test_fleet_activity_empty_when_no_subagents(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A session with no ``subagents/`` dir is the common case — cheap ``()``,
    no message read paid."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    assert adapter.fleet_activity(BASIC_CWD, BASIC_SID) == []


def test_fleet_activity_identity_status_turns_model(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """One sub-agent thread, itemized: identity from the ``.meta.json`` sidecar
    (``agentType``/``description`` → ``title``/``current_task``), the
    parent/child link back to the primary session, per-thread turn counts, and
    the thread's OWN model (verified on-host: a sub-agent frequently runs a
    DIFFERENT model than the main thread, e.g. an ``Explore`` worker on haiku
    under an opus primary) — a status derived from the tail's own block SHAPE,
    since the spine deliberately carries no raw ``stop_reason``."""
    sid = "14141414-1414-4141-8141-141414141414"
    cwd = Path("/home/dev/work/fleet-basic")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
            '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1","name":"Agent",'
            '"input":{"description":"Explore the auth flow","subagent_type":"Explore"}}]}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent01",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-01T10:00:02.000Z",'
            '"message":{"role":"user","content":"Explore the auth flow, report back."}}',
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-01T10:00:03.000Z","message":{"id":"sm1","role":"assistant",'
            '"model":"claude-haiku-4-5-20251001","stop_reason":"tool_use",'
            '"usage":{"input_tokens":10,"output_tokens":5},'
            '"content":[{"type":"tool_use","id":"stu1","name":"Read",'
            '"input":{"file_path":"auth.py"}}]}}',
            '{"type":"user","uuid":"sr1","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-01T10:00:04.000Z","message":{"role":"user","content":['
            '{"type":"tool_result","tool_use_id":"stu1","content":"def login(): ..."}]}}',
            '{"type":"assistant","uuid":"sa2","isSidechain":true,"agentId":"agent01",'
            '"timestamp":"2026-06-01T10:00:05.000Z","message":{"id":"sm2","role":"assistant",'
            '"model":"claude-haiku-4-5-20251001","stop_reason":"end_turn",'
            '"usage":{"input_tokens":20,"output_tokens":15},'
            '"content":[{"type":"text","text":"Auth uses session cookies."}]}}',
        ],
        meta={
            "agentType": "Explore",
            "description": "Explore the auth flow",
            "toolUseId": "tu1",
        },
    )

    fleet = adapter.fleet_activity(cwd, sid)
    assert len(fleet) == 1
    session, act = fleet[0]
    assert session.session_id == "agent01"
    assert session.adapter_kind == "claude_code"
    assert session.parent_session_id == sid
    assert act.title == "Explore"
    assert act.current_task == "Explore the auth flow"
    assert act.human_turns == 1
    assert act.assistant_replies == 2
    assert act.tool_calls == 1
    assert act.model == "claude-haiku-4-5-20251001"
    assert act.tokens_in == 30
    assert act.tokens_out == 20
    # The tail assistant reply ends in plain text (no tool_use) — its own turn
    # closed, mirroring the main thread's end_turn → WAITING rule.
    assert act.state is AgentActivityState.WAITING
    assert act.started_at == datetime.fromisoformat("2026-06-01T10:00:02.000Z")
    assert act.last_event_at == datetime.fromisoformat("2026-06-01T10:00:05.000Z")


def test_fleet_activity_working_when_tail_holds_a_tool_call(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A sub-agent whose tail is still mid tool-loop reads WORKING, not WAITING."""
    sid = "15151515-1515-4151-8151-151515151515"
    cwd = Path("/home/dev/work/fleet-working")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent02",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent02",'
            '"timestamp":"2026-06-01T10:00:01.000Z",'
            '"message":{"role":"user","content":"dig in"}}',
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent02",'
            '"timestamp":"2026-06-01T10:00:02.000Z","message":{"id":"sm1","role":"assistant",'
            '"stop_reason":"tool_use",'
            '"content":[{"type":"tool_use","id":"stu1","name":"Grep","input":{"pattern":"x"}}]}}',
        ],
    )
    fleet = adapter.fleet_activity(cwd, sid)
    assert len(fleet) == 1
    _, act = fleet[0]
    assert act.state is AgentActivityState.WORKING


def test_fleet_activity_falls_back_when_meta_json_missing(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """No ``.meta.json`` sidecar (Claude Code writes it fire-and-forget, so it
    can be absent) → identity degrades honestly: no title, and ``current_task``
    falls back to the truncated first task prompt rather than nothing."""
    sid = "16161616-1616-4161-8161-161616161616"
    cwd = Path("/home/dev/work/fleet-no-meta")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent03",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent03",'
            '"timestamp":"2026-06-01T10:00:01.000Z",'
            '"message":{"role":"user","content":"Investigate the flaky test."}}',
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent03",'
            '"timestamp":"2026-06-01T10:00:02.000Z","message":{"id":"sm1","role":"assistant",'
            '"stop_reason":"end_turn","content":[{"type":"text","text":"It is a race."}]}}',
        ],
        meta=None,
    )
    fleet = adapter.fleet_activity(cwd, sid)
    assert len(fleet) == 1
    session, act = fleet[0]
    assert session.transcript_path is not None
    assert act.title is None
    assert act.current_task == "Investigate the flaky test."


def test_fleet_activity_ignores_malformed_meta_json(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A malformed sidecar degrades to the same fallback as a missing one —
    never raises."""
    sid = "17171717-1717-4171-8171-171717171717"
    cwd = Path("/home/dev/work/fleet-bad-meta")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
        ],
    )
    path = _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent04",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent04",'
            '"timestamp":"2026-06-01T10:00:01.000Z",'
            '"message":{"role":"user","content":"Look at the config loader."}}',
        ],
    )
    path.with_name("agent-agent04.meta.json").write_text("{not json", encoding="utf-8")
    fleet = adapter.fleet_activity(cwd, sid)
    assert len(fleet) == 1
    _, act = fleet[0]
    assert act.current_task == "Look at the config loader."


def test_fleet_activity_multiple_threads_grouped_independently(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Two concurrent sub-agents stay in separate entries, each with its own
    turns/model — a fan-out fleet, not a merged blob."""
    sid = "18181818-1818-4181-8181-181818181818"
    cwd = Path("/home/dev/work/fleet-multi")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agentA",
        [
            '{"type":"user","uuid":"a-u1","isSidechain":true,"agentId":"agentA",'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"role":"user","content":"probe A"}}',
            '{"type":"assistant","uuid":"a-a1","isSidechain":true,"agentId":"agentA",'
            '"timestamp":"2026-06-01T10:00:02.000Z","message":{"id":"a-m1","role":"assistant",'
            '"model":"claude-opus-4-8","stop_reason":"end_turn",'
            '"content":[{"type":"text","text":"A done"}]}}',
        ],
        meta={"agentType": "general-purpose", "description": "probe A", "toolUseId": "tuA"},
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agentB",
        [
            '{"type":"user","uuid":"b-u1","isSidechain":true,"agentId":"agentB",'
            '"timestamp":"2026-06-01T10:00:01.500Z","message":{"role":"user","content":"probe B"}}',
            '{"type":"assistant","uuid":"b-a1","isSidechain":true,"agentId":"agentB",'
            '"timestamp":"2026-06-01T10:00:02.500Z","message":{"id":"b-m1","role":"assistant",'
            '"model":"claude-haiku-4-5-20251001","stop_reason":"end_turn",'
            '"content":[{"type":"text","text":"B done"}]}}',
        ],
        meta={"agentType": "Explore", "description": "probe B", "toolUseId": "tuB"},
    )
    fleet = adapter.fleet_activity(cwd, sid)
    by_id = {session.session_id: (session, act) for session, act in fleet}
    assert set(by_id) == {"agentA", "agentB"}
    assert by_id["agentA"][1].model == "claude-opus-4-8"
    assert by_id["agentB"][1].model == "claude-haiku-4-5-20251001"
    assert all(session.parent_session_id == sid for session, _ in fleet)


def test_subagent_turns_returns_the_threads_own_turns(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A fleet child's own conversation, rendered through the SAME projection
    the main thread's ``turns()`` uses — the turns sibling of
    :meth:`ClaudeCodeAdapter.fleet_activity`, and what makes a fleet row's
    ``session_id`` (the sub-agent thread id) reachable at all, since it never
    appears in any workspace's own session listing (``discover_paths``
    deliberately skips ``subagents/``)."""
    sid = "19191919-1919-4191-8191-191919191919"
    cwd = Path("/home/dev/work/fleet-turns")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent09",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent09",'
            '"timestamp":"2026-06-01T10:00:01.000Z",'
            '"message":{"role":"user","content":"Explore the config loader."}}',
            '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent09",'
            '"timestamp":"2026-06-01T10:00:02.000Z","message":{"id":"sm1","role":"assistant",'
            '"stop_reason":"tool_use",'
            '"content":[{"type":"tool_use","id":"stu1","name":"Read",'
            '"input":{"file_path":"config.py"}}]}}',
            '{"type":"user","uuid":"sr1","isSidechain":true,"agentId":"agent09",'
            '"timestamp":"2026-06-01T10:00:03.000Z","message":{"role":"user","content":['
            '{"type":"tool_result","tool_use_id":"stu1","content":"def load(): ..."}]}}',
            '{"type":"assistant","uuid":"sa2","isSidechain":true,"agentId":"agent09",'
            '"timestamp":"2026-06-01T10:00:04.000Z","message":{"id":"sm2","role":"assistant",'
            '"stop_reason":"end_turn",'
            '"content":[{"type":"text","text":"It loads from config.py."}]}}',
        ],
    )

    turns = adapter.subagent_turns(cwd, sid, "agent09")
    assert len(turns) == 1
    (turn,) = turns
    assert turn.user_text == "Explore the config loader."
    assert [(e.role, e.text) for e in turn.entries] == [
        ("tool", "Read"),
        ("assistant", "It loads from config.py."),
    ]


def test_subagent_turns_empty_for_unknown_thread(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """An id that isn't one of this session's own sub-agent threads degrades
    to ``()``, never raises — the same posture as ``fleet_activity``."""
    sid = "20202020-2020-4202-8202-202020202020"
    cwd = Path("/home/dev/work/fleet-turns-unknown")
    _write_lines(
        claude_home,
        cwd,
        sid,
        [
            '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
        ],
    )
    _write_subagent(
        claude_home,
        cwd,
        sid,
        "agent10",
        [
            '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent10",'
            '"timestamp":"2026-06-01T10:00:01.000Z","message":{"role":"user","content":"go"}}',
        ],
    )
    assert adapter.subagent_turns(cwd, sid, "nonexistent") == ()


def test_subagent_turns_empty_when_no_subagents_dir(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """No ``subagents/`` dir at all is the common case — the cheap early exit
    mirroring ``fleet_activity``'s own no-op."""
    _install(claude_home, BASIC_CWD, BASIC_SID, BASIC)
    assert adapter.subagent_turns(BASIC_CWD, BASIC_SID, "whatever") == ()


def test_claude_offline_decoration_disallows_web_tools() -> None:
    """#148: `tools_offline` maps to Claude Code's `--disallowedTools` flag,
    dropping the two network-facing built-ins."""
    assert get_adapter("claude_code").offline_decoration() == [
        "--disallowedTools",
        "WebFetch,WebSearch",
    ]


# ─── session controls (#178): TIER-1 filesystem enumeration ─────────────────


def test_session_controls_enumerates_commands_skills_mcp(
    adapter: ClaudeCodeAdapter, claude_home: Path, tmp_path: Path
) -> None:
    cwd = tmp_path / "wt"
    commands = cwd / ".claude" / "commands"
    (commands / "git").mkdir(parents=True)
    (commands / "review.md").write_text(
        "---\ndescription: Review the diff\n---\nbody", encoding="utf-8"
    )
    (commands / "git" / "commit.md").write_text("plain body", encoding="utf-8")
    skill_dir = cwd / ".claude" / "skills" / "brainstorming"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\ndescription: Explore ideas\n---\n", encoding="utf-8")
    (cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"gitea": {}, "playwright": {}}}), encoding="utf-8"
    )
    # A user-scoped command under the CLAUDE_CONFIG_DIR base (the cascade).
    (claude_home / "commands").mkdir(parents=True)
    (claude_home / "commands" / "userdoc.md").write_text("u", encoding="utf-8")

    controls = adapter.session_controls(cwd, "sid")

    by_name = {c.name: c for c in controls.commands}
    assert {"review", "git:commit", "userdoc"} <= set(by_name)
    assert by_name["review"].detail == "Review the diff"  # front-matter
    assert by_name["review"].scope == "project"
    assert by_name["userdoc"].scope == "user"
    assert by_name["git:commit"].detail is None  # no front-matter → None
    assert {c.name for c in controls.skills} == {"brainstorming"}
    assert {c.name for c in controls.mcp_servers} == {"gitea", "playwright"}
    # The adapter fills only the fs-scanned lists — model/permission are the
    # manager's to add.
    assert controls.models == ()
    assert controls.current_model is None
    assert controls.permission_mode is None


def test_session_controls_empty_when_no_dot_claude(
    adapter: ClaudeCodeAdapter, claude_home: Path, tmp_path: Path
) -> None:
    del claude_home
    controls = adapter.session_controls(tmp_path / "bare", "sid")
    assert controls.commands == ()
    assert controls.skills == ()
    assert controls.mcp_servers == ()


def test_project_mcp_servers_reads_the_worktrees_committed_registry(
    adapter: ClaudeCodeAdapter, claude_home: Path, tmp_path: Path
) -> None:
    """The bare-names projection of the same scan, in declaration order.

    Public because the container trust stamp pre-approves exactly this list, and
    a second parser for one JSON object is how the two come to disagree. The
    USER registry is not included — a container cannot reach it.
    """
    del claude_home
    cwd = tmp_path / "wt"
    cwd.mkdir()
    (cwd / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"gitea": {}, "playwright": {}}}), encoding="utf-8"
    )

    assert adapter.project_mcp_servers(cwd) == ("gitea", "playwright")
    assert adapter.project_mcp_servers(tmp_path / "bare") == ()


def test_session_controls_tolerates_malformed_mcp_json(
    adapter: ClaudeCodeAdapter, claude_home: Path, tmp_path: Path
) -> None:
    del claude_home
    cwd = tmp_path / "wt"
    cwd.mkdir()
    (cwd / ".mcp.json").write_text("{ not json", encoding="utf-8")
    # Best-effort: a junk file drops that source, never raises.
    assert adapter.session_controls(cwd, "sid").mcp_servers == ()


# ─── incremental re-reads (transcript-cache integration, daemon-CPU fix) ────

INC_SID = "33333333-3333-4333-8333-333333333333"
INC_CWD = Path("/home/dev/work/inc")


def _write_session(claude_home: Path, cwd: Path, sid: str, lines: list[dict]) -> Path:
    target_dir = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sid}.jsonl"
    target.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return target


def _append(target: Path, lines: list[dict]) -> None:
    with target.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(json.dumps(line) + "\n")


def _user(uuid: str, ts: str, text: str) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": ts,
        "isSidechain": False,
        "cwd": str(INC_CWD),
        "sessionId": INC_SID,
        "message": {"role": "user", "content": text},
    }


def _assistant_block(uuid: str, ts: str, msg_id: str, block: dict, *, stop: str) -> dict:
    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": ts,
        "isSidechain": False,
        "sessionId": INC_SID,
        "requestId": f"req-{msg_id}",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "model": "claude-opus-4-8",
            "stop_reason": stop,
            "usage": {"input_tokens": 100, "output_tokens": 10},
            "content": [block],
        },
    }


def test_memo_returns_identical_objects_while_unchanged(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    _write_session(
        claude_home,
        INC_CWD,
        INC_SID,
        [
            _user("u1", "2026-06-01T10:00:00.000Z", "hello"),
            _assistant_block(
                "a1",
                "2026-06-01T10:00:01.000Z",
                "m1",
                {"type": "text", "text": "hi"},
                stop="end_turn",
            ),
        ],
    )
    act = adapter.parse_activity(INC_CWD, INC_SID)
    assert adapter.parse_activity(INC_CWD, INC_SID) is act
    msgs = adapter.read_messages(INC_CWD, INC_SID)
    assert adapter.read_messages(INC_CWD, INC_SID) is msgs


def test_incremental_append_advances_activity(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    target = _write_session(
        claude_home,
        INC_CWD,
        INC_SID,
        [
            _user("u1", "2026-06-01T10:00:00.000Z", "first ask"),
            _assistant_block(
                "a1",
                "2026-06-01T10:00:01.000Z",
                "m1",
                {"type": "text", "text": "done"},
                stop="end_turn",
            ),
        ],
    )
    first = adapter.parse_activity(INC_CWD, INC_SID)
    assert first.human_turns == 1
    assert first.state is AgentActivityState.WAITING

    _append(
        target,
        [
            _user("u2", "2026-06-01T10:01:00.000Z", "second ask"),
            _assistant_block(
                "a2",
                "2026-06-01T10:01:01.000Z",
                "m2",
                {"type": "tool_use", "id": "t9", "name": "Bash", "input": {}},
                stop="tool_use",
            ),
        ],
    )
    second = adapter.parse_activity(INC_CWD, INC_SID)
    assert second.human_turns == 2
    assert second.state is AgentActivityState.WORKING
    # Usage is counted once per logical message across the whole history.
    assert second.tokens_in == 200


def test_split_block_siblings_absorb_once_across_appends(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The absorb-merge (one line per content block) stays correct when the
    sibling lands in a LATER incremental read — and repeated reads never
    re-absorb (the duplicate-blocks hazard the private-dict fold design
    exists to prevent)."""
    target = _write_session(
        claude_home,
        INC_CWD,
        INC_SID,
        [
            _user("u1", "2026-06-01T10:00:00.000Z", "go"),
            _assistant_block(
                "a1",
                "2026-06-01T10:00:01.000Z",
                "m1",
                {"type": "text", "text": "thinking about it"},
                stop="tool_use",
            ),
        ],
    )
    adapter.parse_activity(INC_CWD, INC_SID)

    # The split-block sibling: same (message.id, requestId), distinct uuid.
    _append(
        target,
        [
            _assistant_block(
                "a1-sibling",
                "2026-06-01T10:00:01.500Z",
                "m1",
                {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}},
                stop="tool_use",
            ),
        ],
    )
    merged = adapter.parse_activity(INC_CWD, INC_SID)
    assert merged.assistant_replies == 1
    assert merged.tool_calls == 1
    assert merged.tokens_in == 100  # sibling usage folds, never double-counts

    # Re-reads (memo hit AND a forced re-walk after touching the file) must
    # not re-absorb the sibling into the kept record.
    again = adapter.parse_activity(INC_CWD, INC_SID)
    assert again.tool_calls == 1
    _append(target, [_user("u2", "2026-06-01T10:02:00.000Z", "and then")])
    moved = adapter.parse_activity(INC_CWD, INC_SID)
    assert moved.tool_calls == 1
    assert moved.human_turns == 2


def test_truncated_rewrite_reparses_from_scratch(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    target = _write_session(
        claude_home,
        INC_CWD,
        INC_SID,
        [
            _user("u1", "2026-06-01T10:00:00.000Z", "one"),
            _user("u2", "2026-06-01T10:01:00.000Z", "two"),
        ],
    )
    assert adapter.parse_activity(INC_CWD, INC_SID).human_turns == 2
    # A shorter rewrite (compaction/cleanup) resets the fold state.
    target.write_text(
        json.dumps(_user("u9", "2026-06-01T11:00:00.000Z", "fresh")) + "\n",
        encoding="utf-8",
    )
    assert adapter.parse_activity(INC_CWD, INC_SID).human_turns == 1


# ─── tool-call detail on turn entries ────────────────────────────────────────
#
# Every entry a ``tool_use`` block produced carries its ``ToolCall`` — request,
# response, duration and running-or-settled — so a renderer draws one expander
# with a spinner or a check for every tool call whatever card sits inside it.
# The rule itself is pinned in test_tool_call.py; these pin the WIRING, and in
# particular that Claude's mid-tool shape (an assistant ``tool_use`` flushed
# before its ``tool_result``) is what "running" reads off.

TOOLS_CWD = Path("/home/dev/tools")
TOOLS_SID = "aaaaaaaa-1111-4111-8111-000000000001"


def _tool_use_line(uuid: str, ts: str, calls: list[tuple[str, str, dict[str, object]]]) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "uuid": uuid,
            "timestamp": ts,
            "cwd": str(TOOLS_CWD),
            "message": {
                "id": f"msg_{uuid}",
                "role": "assistant",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "tool_use", "id": cid, "name": name, "input": args}
                    for cid, name, args in calls
                ],
            },
        }
    )


def _tool_result_line(uuid: str, ts: str, cid: str, text: str, *, is_error: bool = False) -> str:
    return json.dumps(
        {
            "type": "user",
            "uuid": uuid,
            "timestamp": ts,
            "cwd": str(TOOLS_CWD),
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": cid,
                        "content": text,
                        "is_error": is_error,
                    }
                ],
            },
        }
    )


def test_tool_entries_carry_request_response_and_duration(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    _write_lines(
        claude_home,
        TOOLS_CWD,
        TOOLS_SID,
        [
            json.dumps(
                {
                    "type": "user",
                    "uuid": "u1",
                    "timestamp": "2026-08-11T10:00:00.000Z",
                    "cwd": str(TOOLS_CWD),
                    "message": {"role": "user", "content": "run the suite"},
                }
            ),
            _tool_use_line(
                "a1", "2026-08-11T10:00:01.000Z", [("t1", "Bash", {"command": "pytest -q"})]
            ),
            _tool_result_line("r1", "2026-08-11T10:00:04.500Z", "t1", "2 passed"),
        ],
    )
    (entry,) = adapter.read_turns(TOOLS_CWD, TOOLS_SID)[0].entries
    assert entry.role == "tool"
    assert entry.tool is not None
    assert entry.tool.name == "Bash"
    assert entry.tool.tool_use_id == "t1"
    assert entry.tool.input == {"command": "pytest -q"}
    assert entry.tool.result == "2 passed"
    assert entry.tool.status == "ok"
    assert entry.tool.duration_ms == 3500


def test_a_failed_tool_reports_error_not_a_successful_call_with_sad_text(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    _write_lines(
        claude_home,
        TOOLS_CWD,
        TOOLS_SID,
        [
            _tool_use_line("a1", "2026-08-11T10:00:01.000Z", [("t1", "Bash", {"command": "nope"})]),
            _tool_result_line("r1", "2026-08-11T10:00:02.000Z", "t1", "Exit code 1", is_error=True),
        ],
    )
    (entry,) = adapter.read_turns(TOOLS_CWD, TOOLS_SID)[0].entries
    assert entry.tool is not None
    assert (entry.tool.status, entry.tool.result) == ("error", "Exit code 1")


def test_a_tool_still_in_flight_reads_running(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Claude flushes the assistant message carrying the ``tool_use`` before the
    result arrives, so a session read mid-tool holds an unresolved call — the
    same shape Codex's open ``function_call`` has. Verified against real on-host
    transcripts (2026-08-11), including a live session's in-flight ``Bash``."""
    _write_lines(
        claude_home,
        TOOLS_CWD,
        TOOLS_SID,
        [
            _tool_use_line(
                "a1", "2026-08-11T10:00:01.000Z", [("t1", "Bash", {"command": "sleep 60"})]
            )
        ],
    )
    (entry,) = adapter.read_turns(TOOLS_CWD, TOOLS_SID)[0].entries
    assert entry.tool is not None
    assert entry.tool.status == "running"
    assert entry.tool.result is None
    assert entry.tool.duration_ms is None
    # The request is available the whole time the call is running — that is the
    # point: a spinner with no arguments is what the report was about.
    assert entry.tool.input == {"command": "sleep 60"}


def test_parallel_tool_calls_stay_individually_correlated(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Claude issues several calls in one assistant message and their results
    come back interleaved; each entry keeps its own id, body and duration."""
    _write_lines(
        claude_home,
        TOOLS_CWD,
        TOOLS_SID,
        [
            _tool_use_line(
                "a1",
                "2026-08-11T10:00:00.000Z",
                [
                    ("t1", "Bash", {"command": "make lint"}),
                    ("t2", "Bash", {"command": "pytest"}),
                ],
            ),
            _tool_result_line("r2", "2026-08-11T10:00:02.000Z", "t2", "green"),
            _tool_result_line("r1", "2026-08-11T10:00:09.000Z", "t1", "clean"),
        ],
    )
    entries = adapter.read_turns(TOOLS_CWD, TOOLS_SID)[0].entries
    assert [(e.tool.tool_use_id, e.tool.result, e.tool.duration_ms) for e in entries if e.tool] == [
        ("t1", "clean", 9000),
        ("t2", "green", 2000),
    ]


def test_a_structured_card_also_carries_its_tool_call(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A diff card says nothing about the invocation that produced it, so the
    file_edit entry carries the call too — that is what lets one expander
    render a spinner-or-check for every tool call, structured or not."""
    _write_lines(
        claude_home,
        TOOLS_CWD,
        TOOLS_SID,
        [
            _tool_use_line(
                "a1",
                "2026-08-11T10:00:00.000Z",
                [("t1", "Edit", {"file_path": "/x/a.py", "old_string": "a", "new_string": "b"})],
            ),
            _tool_result_line("r1", "2026-08-11T10:00:00.750Z", "t1", "ok"),
        ],
    )
    (entry,) = adapter.read_turns(TOOLS_CWD, TOOLS_SID)[0].entries
    assert entry.role == "file_edit"
    assert entry.file_edit is not None
    assert entry.tool is not None
    assert (entry.tool.name, entry.tool.status, entry.tool.duration_ms) == ("Edit", "ok", 750)
