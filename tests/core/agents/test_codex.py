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
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents import AgentActivityState, get_adapter
from grove.core.agents.codex import CodexAdapter, _RolloutParser

FIXTURES = Path(__file__).parent / "fixtures"
BASIC = FIXTURES / "codex_basic.jsonl"
QUESTIONS = FIXTURES / "codex_questions.jsonl"
FILE_EDIT = FIXTURES / "codex_file_edit.jsonl"
TODO = FIXTURES / "codex_todo.jsonl"

# The id + cwd the fixture's session_meta records in-line.
BASIC_SID = "019dd5d5-60fb-7461-bd07-b6e8cf342726"
BASIC_CWD = Path("/home/dev/work/svc")

# The questions fixture's session_meta id (same cwd as BASIC).
QUESTIONS_SID = "019dd6aa-11aa-7461-bd07-aaaaaaaaaaaa"

# The file-edit fixture's session_meta id (same cwd as BASIC).
FILE_EDIT_SID = "019dd777-22bb-7461-bd07-bbbbbbbbbbbb"

# The update_plan fixture's session_meta id (same cwd as BASIC).
TODO_SID = "019dd888-33cc-7461-bd07-cccccccccccc"


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


# ─── agent questions (issue #74 — MCP-bridged question tool in the rollout) ───


def test_question_function_call_becomes_resolved_question_entry(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """A question-shaped ``function_call`` (``AskUserQuestion``, the MCP-bridge
    name) becomes a structured ``role="question"`` entry, resolved by its
    matching ``function_call_output``. Shape normalization, not fabricated
    semantics: Codex never persists native approval prompts."""
    _install(codex_home, QUESTIONS_SID, QUESTIONS)
    (turn,) = adapter.read_turns(BASIC_CWD, QUESTIONS_SID)
    questions = [e for e in turn.entries if e.role == "question"]
    assert len(questions) == 1
    entry = questions[0]
    assert entry.text == "Pick a deploy target"
    assert entry.question is not None
    q = entry.question
    assert q.kind == "single_select"
    assert q.prompt == "Pick a deploy target"
    assert q.header == "Deploy"
    assert len(q.options) == 2
    assert q.options[0].label == "staging"
    assert q.options[1].label == "prod"
    assert q.answered is True
    assert q.answer == "staging"


def test_normal_function_call_still_renders_as_tool(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """A non-question ``function_call`` (e.g. ``exec_command``) is unaffected —
    it still renders as a ``role="tool"`` entry, no regression."""
    _install(codex_home, BASIC_SID, BASIC)
    (turn,) = adapter.read_turns(BASIC_CWD, BASIC_SID)
    assert [(e.role, e.text) for e in turn.entries] == [
        ("assistant", "I'll add the endpoint."),
        ("tool", "exec_command"),
    ]
    assert all(e.question is None for e in turn.entries)


# ─── file edits (apply_patch is a custom_tool_call, verified on-host) ────────


def test_apply_patch_custom_tool_call_becomes_file_edit_entry(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """Codex's ``apply_patch`` file editor is recorded as a ``custom_tool_call``
    (NOT a ``function_call``) whose ``input`` is the raw ``*** Begin Patch`` …
    ``*** End Patch`` body — a plain string, not a JSON ``arguments`` blob
    (verified against real on-host rollouts, codex 0.125.0). It becomes a
    structured ``role="file_edit"`` entry with the old/new bodies reconstructed
    from the patch and the path recovered from the ``*** Update File:`` marker."""
    _install(codex_home, FILE_EDIT_SID, FILE_EDIT)
    (turn,) = adapter.read_turns(BASIC_CWD, FILE_EDIT_SID)
    edits = [e for e in turn.entries if e.role == "file_edit"]
    assert len(edits) == 1
    entry = edits[0]
    assert entry.text == "apply_patch Engines/audit.py"
    assert entry.file_edit is not None
    edit = entry.file_edit
    assert edit.path == "Engines/audit.py"
    # Removed lines land in old_text only; the added line in new_text only.
    assert "if self._is_owner:" in edit.old_text
    assert "if self._is_owner:" not in edit.new_text
    assert "_scope_has_action" in edit.new_text
    assert "_scope_has_action" not in edit.old_text
    # A context line feeds both sides.
    assert "def can_read_any(self):" in edit.old_text
    assert "def can_read_any(self):" in edit.new_text


def test_apply_patch_is_counted_as_a_tool_call(adapter: CodexAdapter, codex_home: Path) -> None:
    """The visibility gap this closes: a ``custom_tool_call`` is now in the
    ``is_tool_call`` set, so an ``apply_patch`` edit is counted (before it was
    silently skipped — uncounted, unrendered). The fixture has exactly one
    ``apply_patch`` plus one other custom tool → two tool calls."""
    _install(codex_home, FILE_EDIT_SID, FILE_EDIT)
    act = adapter.parse_activity(BASIC_CWD, FILE_EDIT_SID)
    assert act.tool_calls == 2


def test_non_edit_custom_tool_call_renders_as_generic_tool(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """Widening ``is_tool_call`` to include ``custom_tool_call`` must not misfire
    the file-edit branch for a non-edit custom tool: a name outside
    ``FILE_EDIT_TOOL_NAMES`` still renders as a plain ``role="tool"`` entry.
    (On-host only ``apply_patch`` is observed as a custom tool today, so
    ``run_notebook`` here is a plausible stand-in guarding future custom tools.)"""
    _install(codex_home, FILE_EDIT_SID, FILE_EDIT)
    (turn,) = adapter.read_turns(BASIC_CWD, FILE_EDIT_SID)
    generic = [e for e in turn.entries if e.role == "tool"]
    assert [e.text for e in generic] == ["run_notebook"]
    assert all(e.file_edit is None for e in generic)


def test_apply_patch_full_turn_entry_shape(adapter: CodexAdapter, codex_home: Path) -> None:
    """The whole turn reads as assistant reply → file_edit card → generic tool,
    in transcript order — the file edit never displaces the surrounding entries."""
    _install(codex_home, FILE_EDIT_SID, FILE_EDIT)
    (turn,) = adapter.read_turns(BASIC_CWD, FILE_EDIT_SID)
    assert turn.user_text == "Refactor the audit read-permission check"
    assert [(e.role, e.text) for e in turn.entries] == [
        ("assistant", "Patching the permission logic."),
        ("file_edit", "apply_patch Engines/audit.py"),
        ("tool", "run_notebook"),
    ]


def test_update_plan_becomes_a_todo_entry(adapter: CodexAdapter, codex_home: Path) -> None:
    """Codex drives its plan through an ``update_plan`` ``function_call`` whose
    ``arguments`` is a JSON string carrying ``plan[]`` with ``step``/``status``
    (verified against a real on-host rollout — no ``content``, no ``activeForm``).
    It renders ONE ``role="todo"`` entry with ``step`` mapped to ``content`` and
    ``active_form`` left ``None``; before #184 it was a bare ``role="tool"``
    "update_plan" and the plan was discarded."""
    _install(codex_home, TODO_SID, TODO)
    (turn,) = adapter.read_turns(BASIC_CWD, TODO_SID)
    todos = [e for e in turn.entries if e.role == "todo"]
    assert len(todos) == 1
    assert todos[0].text == "1/3 done · Design a minimal KISS refactor"
    assert todos[0].todo is not None
    assert [(i.content, i.status, i.active_form) for i in todos[0].todo.items] == [
        ("Review current orchestration code", "completed", None),
        ("Design a minimal KISS refactor", "in_progress", None),
        ("Implement and add tests", "pending", None),
    ]
    # The whole turn: assistant reply → todo card, in order; never a generic tool.
    assert [(e.role, e.text) for e in turn.entries] == [
        ("assistant", "Laying out a plan."),
        ("todo", "1/3 done · Design a minimal KISS refactor"),
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


def test_discover_births_reads_birth_from_meta_head(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """#F5: ``discover_births`` pairs each rollout with its BIRTH (first head
    record timestamp) from the same bounded ``session_meta`` head read — no full
    parse — so the adoption gate rejects history cheaply. Newest-first by mtime."""
    cwd = Path("/home/dev/work/births")
    older = "0aaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa"
    newer = "0fffffff-ffff-7fff-8fff-ffffffffffff"
    births = {older: "2026-04-28T20:00:00.000Z", newer: "2026-04-29T09:15:00.000Z"}
    for sid, mtime in ((older, 1000), (newer, 2000)):
        path = _install_text(
            codex_home,
            sid,
            '{"timestamp":"' + births[sid] + '","type":"session_meta",'
            '"payload":{"id":"' + sid + '","cwd":"' + str(cwd) + '","model_provider":"openai"}}\n',
        )
        os.utime(path, (mtime, mtime))

    result = adapter.discover_births(cwd)
    assert [sid for sid, _birth, _mtime in result] == [newer, older]
    by_id = {sid: birth for sid, birth, _mtime in result}
    assert by_id[newer] == datetime(2026, 4, 29, 9, 15, tzinfo=UTC)
    assert by_id[older] == datetime(2026, 4, 28, 20, 0, tzinfo=UTC)


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


def test_codex_model_decoration_is_model_flag() -> None:
    """Codex honors ``--model <id>`` at launch even though it mints no id (#96)."""
    assert get_adapter("codex").model_decoration("gpt-5.5") == ["--model", "gpt-5.5"]


def test_codex_offline_decoration_disables_sandbox_network() -> None:
    """#148: Codex has no standalone tool-disable flag — `tools_offline` pins
    the sandbox to workspace-write with networking off instead."""
    assert get_adapter("codex").offline_decoration() == [
        "--sandbox",
        "workspace-write",
        "-c",
        "sandbox_workspace_write.network_access=false",
    ]


def _install_text(codex_home: Path, sid: str, text: str) -> Path:
    target_dir = codex_home / "sessions" / "2026" / "04" / "28"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"rollout-2026-04-28T13-43-44-{sid}.jsonl"
    target.write_text(text, encoding="utf-8")
    return target


# ─── the agentic-loop spine (#179) ──────────────────────────────────────────


def test_read_messages_maps_roles_and_drops_metadata(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """The spine is the parse ``read_turns`` / ``digest`` project from: the
    ``session_meta`` / ``turn_context`` metadata and the ``event_msg`` status /
    token mirrors are dropped, the developer message and AGENTS.md preamble user
    message are not turns, and each surviving native line becomes one message —
    the real prompt, opaque reasoning (a ``thinking`` block), the assistant
    message, the ``function_call`` (a ``tool_use`` block) and its output."""
    _install(codex_home, BASIC_SID, BASIC)
    messages = adapter.read_messages(BASIC_CWD, BASIC_SID)

    assert [m.role for m in messages] == ["user", "assistant", "assistant", "assistant", "tool"]

    # Codex reports usage per SESSION, not per message, and has no logical
    # message id — those stay unset (never fabricated from the cumulative total).
    assert all(m.usage is None for m in messages)
    assert all(m.message_id is None for m in messages)

    # Reasoning is opaque (encrypted_content) → a thinking block with no readable
    # text, never the decrypted payload.
    reasoning = messages[1].content[0]
    assert reasoning.type == "thinking"
    assert reasoning.text is None

    # The function_call is a tool_use block carrying the correlating call_id.
    tool_use = messages[3].content[0]
    assert tool_use.type == "tool_use"
    assert tool_use.tool_name == "exec_command"
    assert tool_use.tool_use_id == "call_gdMNxpyvIKl8Fo6w2xOE65LH"

    # Its output is a tool_result carrier resolving the same call_id.
    output = messages[4]
    assert output.role == "tool"
    assert output.content[0].type == "tool_result"
    assert output.content[0].tool_use_id == "call_gdMNxpyvIKl8Fo6w2xOE65LH"


def test_read_messages_maps_apply_patch_custom_tool_call(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """``apply_patch`` is a ``custom_tool_call`` whose ``input`` is the raw patch
    body (not JSON). The spine carries it as a ``tool_use`` block with the body
    wrapped as ``{"input": <patch>}`` — the shape ``FileEdit.from_tool_call``
    consumes — so the file-edit projection renders across the same seam."""
    _install(codex_home, FILE_EDIT_SID, FILE_EDIT)
    messages = adapter.read_messages(BASIC_CWD, FILE_EDIT_SID)

    patch_blocks = [
        b
        for m in messages
        for b in m.content
        if b.type == "tool_use" and b.tool_name == "apply_patch"
    ]
    assert len(patch_blocks) == 1
    assert patch_blocks[0].tool_input is not None
    assert isinstance(patch_blocks[0].tool_input.get("input"), str)


def test_read_turns_is_a_projection_of_read_messages(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """The turn projection reads the same spine: every ``user`` message's text
    is a turn's ``user_text`` (one parse, many projections)."""
    _install(codex_home, BASIC_SID, BASIC)
    messages = adapter.read_messages(BASIC_CWD, BASIC_SID)
    spine_prompts = [m.text() for m in messages if m.role == "user"]
    turn_prompts = [t.user_text for t in adapter.read_turns(BASIC_CWD, BASIC_SID)]
    assert turn_prompts == spine_prompts


# ─── typed final-result extraction (#149) ───────────────────────────────────


def test_final_result_incomplete_when_a_tool_result_trails_the_last_assistant(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """The BASIC fixture's spine tail is the ``tool`` result carrier, one past
    the last ``assistant``-role message (the ``function_call``) — so
    ``is_complete`` is ``False`` even though ``task_complete`` fired in the raw
    rollout: that boundary lives only in ``event_msg`` records, which never
    reach the spine (#179's dual-record rule), so the shape-only projection is
    the honest best-effort answer here, not the event-accurate one."""
    _install(codex_home, BASIC_SID, BASIC)
    result = adapter.final_result(BASIC_CWD, BASIC_SID)
    assert result is not None
    assert result.is_complete is False


def test_final_result_complete_on_a_trailing_plain_text_reply(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """A tail assistant ``message`` with no following tool call is the
    terminal shape — complete, carrying that reply's text."""
    sid = "55555555-5555-7555-8555-555555555555"
    rollout = (
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + sid + '","cwd":"/home/dev/work/done","model_provider":"openai"}}\n'
        '{"timestamp":"2026-04-28T20:00:01.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"add the endpoint"}]}}\n'
        '{"timestamp":"2026-04-28T20:00:02.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"assistant",'
        '"content":[{"type":"output_text","text":"Done, added it."}]}}\n'
    )
    _install_text(codex_home, sid, rollout)
    result = adapter.final_result(Path("/home/dev/work/done"), sid)
    assert result is not None
    assert result.is_complete is True
    assert result.text == "Done, added it."


def test_final_result_none_before_any_assistant_reply(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """No assistant has replied yet — degrade to ``None``."""
    sid = "66666666-6666-7666-8666-666666666666"
    rollout = (
        '{"timestamp":"2026-04-28T20:00:00.000Z","type":"session_meta",'
        '"payload":{"id":"' + sid + '","cwd":"/home/dev/work/none","model_provider":"openai"}}\n'
        '{"timestamp":"2026-04-28T20:00:01.000Z","type":"response_item",'
        '"payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"go"}]}}\n'
    )
    _install_text(codex_home, sid, rollout)
    assert adapter.final_result(Path("/home/dev/work/none"), sid) is None


# ─── latest-todo projection (#194) ──────────────────────────────────────────


def test_latest_todo_from_an_update_plan_call(adapter: CodexAdapter, codex_home: Path) -> None:
    """``update_plan`` (Codex's ``plan[]``/``step``/``status`` shape, no
    ``content``/``activeForm``) is whole-list-per-call, same as Claude's
    ``TodoWrite`` — the provider-neutral projection reads it straight
    through with no Task-system fold involved."""
    _install(codex_home, TODO_SID, TODO)
    todo = adapter.latest_todo(BASIC_CWD, TODO_SID)
    assert todo is not None
    assert [(i.content, i.status, i.active_form) for i in todo.items] == [
        ("Review current orchestration code", "completed", None),
        ("Design a minimal KISS refactor", "in_progress", None),
        ("Implement and add tests", "pending", None),
    ]


def test_latest_todo_none_before_any_todo_call(adapter: CodexAdapter, codex_home: Path) -> None:
    """The BASIC fixture never calls ``update_plan`` — degrade to ``None``."""
    _install(codex_home, BASIC_SID, BASIC)
    assert adapter.latest_todo(BASIC_CWD, BASIC_SID) is None


# ─── session controls (#178): codex analog (prompts + config.toml MCP) ──────


def test_session_controls_enumerates_prompts_and_mcp(
    adapter: CodexAdapter, codex_home: Path, tmp_path: Path
) -> None:
    prompts = codex_home / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "plan.md").write_text("plan prompt", encoding="utf-8")
    (prompts / "ship.md").write_text("ship prompt", encoding="utf-8")
    (codex_home / "config.toml").write_text(
        '[mcp_servers.gitea]\ncommand = "x"\n[mcp_servers.fs]\ncommand = "y"\n',
        encoding="utf-8",
    )

    controls = adapter.session_controls(tmp_path / "repo", "sid")

    assert {c.name for c in controls.commands} == {"plan", "ship"}
    assert all(c.scope == "user" for c in controls.commands)
    assert {c.name for c in controls.mcp_servers} == {"gitea", "fs"}
    assert controls.skills == ()  # codex has no skills concept


def test_session_controls_empty_and_tolerant(
    adapter: CodexAdapter, codex_home: Path, tmp_path: Path
) -> None:
    # No prompts dir + malformed config.toml → honest empty, never raises.
    (codex_home).mkdir(parents=True, exist_ok=True)
    (codex_home / "config.toml").write_text("not = [valid toml", encoding="utf-8")
    controls = adapter.session_controls(tmp_path / "repo", "sid")
    assert controls.commands == ()
    assert controls.mcp_servers == ()
