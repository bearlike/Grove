"""The canonical shell observation, across harnesses and across evidence states.

The claim under test is a CROSS-HARNESS one: a Claude `Bash` call and a Codex
`exec_command` call, replayed from their own transcript shapes, must land on the
same category, the same metadata keys and the same `command`/`content` paths —
because an evaluator is one rule with one variable mapping and it cannot hold a
per-harness special case. So the harness-parity tests here assert the two
against EACH OTHER rather than each against a literal: two literals is exactly
how a test agrees with itself while the two producers disagree.

Everything is synthetic. Nothing here runs a shell, provisions anything, or
touches a network — the payloads are fixtures describing commands, never
commands.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.model import AgentMessage, ContentBlock
from grove.core.agents.shell import SHELL_TOOL_NAMES, ShellCall
from grove.core.config import TelemetryConfig, TelemetryContent
from grove.core.telemetry.semconv import (
    ATTR_TEXT_CAP,
    GenAiAttr,
    GroveIdentityAttr,
    LangfuseAttr,
    TraceIdentity,
    filterable_identity,
)
from grove.core.telemetry.shell import (
    INPUT_COMMAND_PATH,
    OUTPUT_CONTENT_PATH,
    SHELL_CATEGORY,
    SHELL_SCHEMA,
    ShellAttr,
    ShellObservation,
    ShellOutcome,
    ShellResult,
    ToolSource,
    _fit,
)
from grove.core.trace import SpanRecord, TraceInstrumentor


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


class _Spine:
    """A `SpineAdapter` over hand-built messages — one harness kind per instance."""

    def __init__(self, kind: str, messages: tuple[AgentMessage, ...]) -> None:
        self.kind = kind
        self._messages = messages

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return self._messages


def _turn(
    *,
    tool_name: str,
    tool_input: dict[str, object],
    result_text: str | None,
    is_error: bool = False,
    exit_code: int | None = None,
) -> tuple[AgentMessage, ...]:
    """One complete human turn whose assistant reply makes a single tool call.

    A turn is only exported once it is COMPLETE, which is why the closing
    assistant message is here rather than being an optional extra: a fixture
    that stops at the tool call exercises nothing at all.
    """
    result_blocks: tuple[ContentBlock, ...] = ()
    if result_text is not None:
        result_blocks = (
            ContentBlock(
                type="tool_result",
                tool_use_id="call-1",
                text=result_text,
                is_error=is_error,
                exit_code=exit_code,
            ),
        )
    messages = [
        AgentMessage(
            role="user",
            timestamp=_ts("2026-09-07T10:00:00"),
            content=(ContentBlock(type="text", text="do the thing"),),
        ),
        AgentMessage(
            role="assistant",
            model="m-1",
            timestamp=_ts("2026-09-07T10:00:01"),
            content=(
                ContentBlock(
                    type="tool_use",
                    tool_name=tool_name,
                    tool_use_id="call-1",
                    tool_input=tool_input,
                ),
            ),
        ),
    ]
    if result_blocks:
        messages.append(
            AgentMessage(role="tool", timestamp=_ts("2026-09-07T10:00:02"), content=result_blocks)
        )
    messages.append(
        AgentMessage(
            role="assistant",
            model="m-1",
            timestamp=_ts("2026-09-07T10:00:03"),
            content=(ContentBlock(type="text", text="done"),),
        )
    )
    return tuple(messages)


def _tool_spans(
    kind: str,
    messages: tuple[AgentMessage, ...],
    *,
    content: TelemetryContent = "all",
    identity: TraceIdentity | None = None,
) -> list[SpanRecord]:
    manifests = TraceInstrumentor(TelemetryConfig(enabled=True), sink=lambda record: None).plan(
        Path("/work"),
        f"sess-{kind}",
        _Spine(kind, messages),
        content=content,
        identity=identity,
    )
    return [span for manifest in manifests for span in manifest.spans if span.kind == "tool"]


def _only_tool(kind: str, messages: tuple[AgentMessage, ...], **kwargs: object) -> SpanRecord:
    spans = _tool_spans(kind, messages, **kwargs)  # type: ignore[arg-type]
    assert len(spans) == 1
    return spans[0]


# ─── the shape seam: what IS a shell call, and what did it ask for ───────────


def test_classification_is_by_exact_tool_name_and_never_by_payload() -> None:
    assert ShellCall.of("Bash", {"command": "ls"}) is not None
    assert ShellCall.of("exec_command", {"cmd": "ls"}) is not None
    # A tool whose arguments happen to carry a command key is still not a shell
    # tool, and a name that merely CONTAINS one is not one either.
    assert ShellCall.of("WebFetch", {"command": "ls"}) is None
    assert ShellCall.of("BashOutput", {"bash_id": "b1"}) is None
    assert ShellCall.of("KillShell", {"shell_id": "b1"}) is None
    assert ShellCall.of("write_stdin", {"cmd": "y\n"}) is None
    assert "BashOutput" not in SHELL_TOOL_NAMES
    assert "write_stdin" not in SHELL_TOOL_NAMES


def test_argv_survives_as_argv_rather_than_becoming_an_allegedly_equal_line() -> None:
    call = ShellCall.of("local_shell", {"cmd": ["bash", "-lc", "echo 'a  b' && ls"]})
    assert call is not None
    assert call.argv == ("bash", "-lc", "echo 'a  b' && ls")
    # Rendered with shell quoting, so the line means what the argv meant. A
    # space join would produce `bash -lc echo 'a  b' && ls`, which is a
    # different command.
    assert call.command == """bash -lc 'echo '"'"'a  b'"'"' && ls'"""


def test_a_harness_without_a_background_flag_is_not_reported_as_declining_one() -> None:
    assert ShellCall.of("Bash", {"command": "x", "run_in_background": True}).background is True  # type: ignore[union-attr]
    assert ShellCall.of("Bash", {"command": "x", "run_in_background": False}).background is False  # type: ignore[union-attr]
    assert ShellCall.of("exec_command", {"cmd": "x"}).background is None  # type: ignore[union-attr]


# ─── cross-harness parity ────────────────────────────────────────────────────


def test_two_harnesses_land_on_one_category_and_one_set_of_property_paths() -> None:
    claude = _only_tool(
        "claude_code",
        _turn(
            tool_name="Bash",
            tool_input={"command": "pytest -q", "description": "run tests"},
            result_text="2 passed",
        ),
    )
    codex = _only_tool(
        "codex",
        _turn(
            tool_name="exec_command",
            tool_input={"cmd": "pytest -q"},
            result_text="2 passed",
            exit_code=0,
        ),
    )

    for span in (claude, codex):
        assert span.attributes[ShellAttr.CATEGORY] == SHELL_CATEGORY
        assert span.attributes[ShellAttr.SCHEMA] == SHELL_SCHEMA
        assert span.attributes[ShellAttr.SOURCE] == ToolSource.TRANSCRIPT
        assert (
            json.loads(str(span.attributes[LangfuseAttr.OBSERVATION_INPUT]))[INPUT_COMMAND_PATH]
            == "pytest -q"
        )
        assert (
            json.loads(str(span.attributes[LangfuseAttr.OBSERVATION_OUTPUT]))[OUTPUT_CONTENT_PATH]
            == "2 passed"
        )

    # Asserted against EACH OTHER: the claim is parity, and two independent
    # assertions against a literal cannot fail when the two producers diverge.
    # The comparison is over the SHAPE keys — the ones every shell observation
    # must carry. Evidence keys (exit code, error flag, background) are
    # deliberately excluded, because a harness that records fewer facts must be
    # allowed to publish fewer of them.
    shape_keys = {
        ShellAttr.CATEGORY,
        ShellAttr.SCHEMA,
        ShellAttr.SOURCE,
        ShellAttr.TOOL_NAME,
        ShellAttr.CALL_ID,
        ShellAttr.RESULT,
        ShellAttr.OUTCOME,
    }
    assert shape_keys <= set(claude.attributes)
    assert shape_keys & set(claude.attributes) == shape_keys & set(codex.attributes)
    assert claude.attributes[ShellAttr.RESULT] == codex.attributes[ShellAttr.RESULT]

    # The provider's OWN identity is preserved beside the canonical shape.
    assert claude.attributes[ShellAttr.TOOL_NAME] == "Bash"
    assert codex.attributes[ShellAttr.TOOL_NAME] == "exec_command"
    assert claude.attributes[GenAiAttr.TOOL_CALL_ID] == "call-1"
    assert claude.attributes[ShellAttr.CALL_ID] == "call-1"
    # ...as is which harness ran it, which is what a dashboard splits scores by.
    kind_key = LangfuseAttr.metadata(GroveIdentityAttr.AGENT_KIND)
    assert claude.attributes[kind_key] == "claude_code"
    assert codex.attributes[kind_key] == "codex"


def test_the_raw_provider_arguments_ride_the_conventions_own_keys_untouched() -> None:
    span = _only_tool(
        "claude_code",
        _turn(
            tool_name="Bash",
            tool_input={"command": "ls", "description": "look"},
            result_text="a\nb",
        ),
    )
    assert json.loads(str(span.attributes[GenAiAttr.TOOL_CALL_ARGUMENTS])) == {
        "command": "ls",
        "description": "look",
    }
    assert span.attributes[GenAiAttr.TOOL_CALL_RESULT] == "a\nb"
    # And they are carried INSIDE the envelope too, so a judge reading only the
    # observation input still sees everything the harness said.
    assert json.loads(str(span.attributes[LangfuseAttr.OBSERVATION_INPUT]))["arguments"] == {
        "command": "ls",
        "description": "look",
    }


def test_a_non_shell_tool_is_left_exactly_as_it_was() -> None:
    span = _only_tool(
        "claude_code",
        _turn(tool_name="Read", tool_input={"file_path": "x.py"}, result_text="contents"),
    )
    assert not any(
        key.startswith(LangfuseAttr.OBSERVATION_METADATA_PREFIX) for key in span.attributes
    )
    # Its payloads stay the raw shapes every existing consumer already reads.
    assert span.attributes[LangfuseAttr.OBSERVATION_OUTPUT] == "contents"


# ─── the evidence states ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("result_text", "exit_code", "is_error", "result", "outcome"),
    [
        (None, None, False, ShellResult.PENDING, ShellOutcome.PENDING),
        ("", None, False, ShellResult.EMPTY, ShellOutcome.UNKNOWN),
        ("out", None, False, ShellResult.CAPTURED, ShellOutcome.UNKNOWN),
        ("out", 0, False, ShellResult.CAPTURED, ShellOutcome.SUCCEEDED),
        ("out", 1, False, ShellResult.CAPTURED, ShellOutcome.FAILED),
        ("out", None, True, ShellResult.CAPTURED, ShellOutcome.FAILED),
        ("", 0, False, ShellResult.EMPTY, ShellOutcome.SUCCEEDED),
    ],
)
def test_result_evidence_and_process_outcome_are_reported_separately(
    result_text: str | None,
    exit_code: int | None,
    is_error: bool,
    result: ShellResult,
    outcome: ShellOutcome,
) -> None:
    """Two axes, because they answer two questions.

    `EMPTY` + `SUCCEEDED` is the case that proves it: a command that printed
    nothing and exited zero is a complete, successful call, and a single enum
    would have to pick one of those two facts to publish.
    """
    span = _only_tool(
        "codex",
        _turn(
            tool_name="exec_command",
            tool_input={"cmd": "x"},
            result_text=result_text,
            is_error=is_error,
            exit_code=exit_code,
        ),
    )
    assert span.attributes[ShellAttr.RESULT] == result
    assert span.attributes[ShellAttr.OUTCOME] == outcome


def test_an_unfinished_call_omits_the_output_and_a_finished_empty_one_does_not() -> None:
    pending = _only_tool(
        "codex", _turn(tool_name="exec_command", tool_input={"cmd": "x"}, result_text=None)
    )
    empty = _only_tool(
        "codex", _turn(tool_name="exec_command", tool_input={"cmd": "x"}, result_text="")
    )
    assert LangfuseAttr.OBSERVATION_OUTPUT not in pending.attributes
    assert json.loads(str(empty.attributes[LangfuseAttr.OBSERVATION_OUTPUT])) == {
        OUTPUT_CONTENT_PATH: "",
        "truncated": False,
    }


def test_an_exit_code_is_carried_only_when_the_harness_recorded_one() -> None:
    with_code = _only_tool(
        "codex",
        _turn(tool_name="exec_command", tool_input={"cmd": "x"}, result_text="y", exit_code=2),
    )
    without = _only_tool(
        "claude_code", _turn(tool_name="Bash", tool_input={"command": "x"}, result_text="y")
    )
    assert with_code.attributes[ShellAttr.EXIT_CODE] == 2
    assert ShellAttr.EXIT_CODE not in without.attributes
    # And absence never becomes a success claim.
    assert without.attributes[ShellAttr.OUTCOME] == ShellOutcome.UNKNOWN


def test_a_harness_that_cannot_flag_a_tool_error_publishes_no_error_claim() -> None:
    """`is_error=False` is not evidence, so it is not published.

    Codex records no structural tool-error flag on a shell result at all, so a
    `false` on the wire would be read as "this call was fine" by every consumer
    that does not know which harness wrote it.
    """
    clean = _only_tool(
        "codex", _turn(tool_name="exec_command", tool_input={"cmd": "x"}, result_text="y")
    )
    flagged = _only_tool(
        "claude_code",
        _turn(tool_name="Bash", tool_input={"command": "x"}, result_text="y", is_error=True),
    )
    assert ShellAttr.ERROR not in clean.attributes
    assert flagged.attributes[ShellAttr.ERROR] is True


# ─── commands and results that are awkward on purpose ────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        'grep -R "needle in a haystack" .',
        "python - <<'EOF'\nprint('hi')\nEOF",
        "echo 'it'\\''s fine' && printf '%s\\n' \"a b\"",
        'for f in *.py; do\n  ruff check "$f"\ndone',
    ],
)
def test_multiline_and_quoted_commands_round_trip_through_the_envelope(command: str) -> None:
    span = _only_tool(
        "claude_code",
        _turn(tool_name="Bash", tool_input={"command": command}, result_text="ok"),
    )
    payload = json.loads(str(span.attributes[LangfuseAttr.OBSERVATION_INPUT]))
    assert payload[INPUT_COMMAND_PATH] == command
    assert payload["truncated"] is False


def test_json_looking_output_stays_TEXT_so_the_envelope_shape_never_varies() -> None:
    """A command that prints JSON is still a command that printed text.

    Parsing it into the envelope would make the document's shape depend on what
    the process happened to write, so one rubric would face a different
    structure per invocation.
    """
    stdout = '{"ok": true, "items": [1, 2]}'
    span = _only_tool(
        "codex",
        _turn(tool_name="exec_command", tool_input={"cmd": "cat r.json"}, result_text=stdout),
    )
    payload = json.loads(str(span.attributes[LangfuseAttr.OBSERVATION_OUTPUT]))
    assert payload[OUTPUT_CONTENT_PATH] == stdout
    assert isinstance(payload[OUTPUT_CONTENT_PATH], str)


# ─── truncation and redaction ────────────────────────────────────────────────


def test_an_oversized_result_is_clipped_into_VALID_json_that_says_it_was() -> None:
    span = _only_tool(
        "codex",
        _turn(
            tool_name="exec_command",
            tool_input={"cmd": "cat huge.log"},
            result_text="x" * (ATTR_TEXT_CAP * 3),
        ),
    )
    encoded = str(span.attributes[LangfuseAttr.OBSERVATION_OUTPUT])
    assert len(encoded) <= ATTR_TEXT_CAP
    payload = json.loads(encoded)  # the assertion: it still PARSES
    assert payload["truncated"] is True
    assert payload[OUTPUT_CONTENT_PATH].startswith("x")
    assert span.attributes[ShellAttr.RESULT] == ShellResult.TRUNCATED


def test_an_oversized_command_keeps_the_command_and_drops_the_arguments_mirror() -> None:
    """Under pressure the envelope sheds the copy, never the thing being graded."""
    command = "echo " + "y" * (ATTR_TEXT_CAP * 2)
    span = _only_tool(
        "claude_code",
        _turn(
            tool_name="Bash",
            tool_input={"command": command, "description": "z" * 500},
            result_text="ok",
        ),
    )
    encoded = str(span.attributes[LangfuseAttr.OBSERVATION_INPUT])
    assert len(encoded) <= ATTR_TEXT_CAP
    payload = json.loads(encoded)
    assert payload["truncated"] is True
    assert payload[INPUT_COMMAND_PATH].startswith("echo yyy")
    assert "arguments" not in payload


def test_escaping_is_accounted_for_when_a_payload_is_shrunk() -> None:
    """A budget computed from the UNESCAPED length overflows on exactly the
    multi-line output this cap exists to carry — one newline costs two encoded
    characters, and a 4000-newline result would encode to 8000."""
    span = _only_tool(
        "codex",
        _turn(
            tool_name="exec_command",
            tool_input={"cmd": "yes"},
            result_text="\n" * (ATTR_TEXT_CAP * 2),
        ),
    )
    encoded = str(span.attributes[LangfuseAttr.OBSERVATION_OUTPUT])
    assert len(encoded) <= ATTR_TEXT_CAP
    assert json.loads(encoded)["truncated"] is True


def test_a_payload_stays_parseable_even_when_the_envelope_alone_exceeds_the_cap() -> None:
    """Validity is the guarantee; the cap is best effort.

    Unreachable at the shipped cap, where the envelope's own keys cost about
    45 characters. Pinned so a future caller with a tighter cap finds out that
    it gets a small valid document rather than a fragment.
    """
    encoded, truncated = _fit(
        {OUTPUT_CONTENT_PATH: "x" * 100, "truncated": False, "exit_code": 0},
        text_key=OUTPUT_CONTENT_PATH,
        cap=40,
    )
    assert truncated is True
    assert json.loads(encoded) == {OUTPUT_CONTENT_PATH: "", "truncated": True, "exit_code": 0}


def test_an_oversized_non_shell_argument_blob_is_a_valid_envelope_not_brace_soup() -> None:
    span = _only_tool(
        "claude_code",
        _turn(
            tool_name="Edit",
            tool_input={"file_path": "x.py", "new_string": "q" * (ATTR_TEXT_CAP * 2)},
            result_text="ok",
        ),
    )
    payload = json.loads(str(span.attributes[GenAiAttr.TOOL_CALL_ARGUMENTS]))
    assert payload["truncated"] is True
    assert payload["original_length"] > ATTR_TEXT_CAP
    assert payload["preview"].startswith("{")


def test_a_redacting_content_policy_keeps_the_outcome_and_drops_the_payloads() -> None:
    span = _only_tool(
        "codex",
        _turn(
            tool_name="exec_command",
            tool_input={"cmd": "printenv"},
            result_text="SECRET=hunter2",
            exit_code=0,
        ),
        content="messages",
    )
    assert LangfuseAttr.OBSERVATION_INPUT not in span.attributes
    assert LangfuseAttr.OBSERVATION_OUTPUT not in span.attributes
    assert "hunter2" not in json.dumps(span.attributes, default=str)
    # The call still shows up as a shell call that succeeded — an outcome is a
    # fact about the invocation, not a payload. And it reads REDACTED, not
    # PENDING: folding the two would report a redacting fleet as permanently
    # mid-command.
    assert span.attributes[ShellAttr.CATEGORY] == SHELL_CATEGORY
    assert span.attributes[ShellAttr.OUTCOME] == ShellOutcome.SUCCEEDED
    assert span.attributes[ShellAttr.RESULT] == ShellResult.REDACTED


# ─── selection: cohorts, duplicates and sub-agents ───────────────────────────


def test_identity_is_written_flat_because_the_nested_copy_is_not_queryable() -> None:
    identity = TraceIdentity(
        workspace_id="ws-1", repo="acme/widget", branch="feat/x", agent_kind="codex"
    )
    span = _only_tool(
        "codex",
        _turn(tool_name="exec_command", tool_input={"cmd": "x"}, result_text="y"),
        identity=identity,
    )
    flat = filterable_identity(identity.attributes())
    assert flat
    for key, value in flat.items():
        assert key.startswith(LangfuseAttr.OBSERVATION_METADATA_PREFIX)
        assert "." not in key[len(LangfuseAttr.OBSERVATION_METADATA_PREFIX) :]
        assert span.attributes[key] == value
    # The original `grove.*` attributes are still there — the flat copy is a
    # second projection, never a replacement.
    assert span.attributes[GroveIdentityAttr.WORKSPACE_ID] == "ws-1"


def test_the_source_tag_is_what_keeps_one_invocation_out_of_a_cohort_twice() -> None:
    """Two tiers can describe one physical call; a cohort must hold one of them.

    This is asserted on the fixtures rather than assumed from deterministic
    span ids: identical ids make reconciliation possible, they do not make OTLP
    ingestion idempotent, so a backend CAN hold both records.
    """
    replayed = _only_tool(
        "claude_code",
        _turn(tool_name="Bash", tool_input={"command": "echo hi"}, result_text="hi"),
    )
    native = SpanRecord.tool(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        name="Bash",
        start_time=_ts("2026-09-07T10:00:01"),
        end_time=_ts("2026-09-07T10:00:02"),
        tool_call_id="call-1",
        tool_input={"full_command": "echo hi"},
        tool_output="hi",
        source=ToolSource.NATIVE_OTLP,
        agent_kind="claude_code",
    )
    # Same cohort, same command path — so the category alone would grade twice.
    assert replayed.attributes[ShellAttr.CATEGORY] == native.attributes[ShellAttr.CATEGORY]
    assert (
        json.loads(str(replayed.attributes[LangfuseAttr.OBSERVATION_INPUT]))[INPUT_COMMAND_PATH]
        == json.loads(str(native.attributes[LangfuseAttr.OBSERVATION_INPUT]))[INPUT_COMMAND_PATH]
    )
    # ...and the one attribute that separates them.
    assert replayed.attributes[ShellAttr.SOURCE] == ToolSource.TRANSCRIPT
    assert native.attributes[ShellAttr.SOURCE] == ToolSource.NATIVE_OTLP
    assert replayed.attributes[ShellAttr.SOURCE] != native.attributes[ShellAttr.SOURCE]


def test_an_observation_grove_never_normalized_carries_no_category_at_all() -> None:
    """The exclusion filter for a foreign producer is structural.

    An external transcript-exporting hook running beside Grove writes its own
    traces, and Grove neither disables it nor rewrites it. Selecting on the
    category is what keeps those out of an evaluation cohort — so the category
    must never appear on anything Grove did not normalize.
    """
    foreign = SpanRecord.tool(
        trace_id=1,
        span_id=2,
        parent_span_id=1,
        name="run_terminal_cmd",  # some other harness's shell verb
        start_time=_ts("2026-09-07T10:00:01"),
        end_time=_ts("2026-09-07T10:00:02"),
        tool_input={"command": "echo hi"},
        tool_output="hi",
        source=ToolSource.TRANSCRIPT,
    )
    assert ShellAttr.CATEGORY not in foreign.attributes


def test_a_sub_agents_shell_call_carries_the_same_shape_and_its_own_provenance(
    claude_home: Path,
) -> None:
    """Driven through the REAL adapter, because the spawn edge is a real file.

    The fleet walk reads a sub-agent sidecar off disk, so a hand-built spine
    cannot reach this path at all — a synthetic sidechain message is silently
    dropped, and a test built on one passes by asserting on the root's call
    twice.
    """
    sid = "5e5e5e5e-5e5e-4e5e-8e5e-5e5e5e5e5e5e"
    cwd = Path("/home/dev/work/shell-fleet")
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.jsonl").write_text(
        "\n".join(
            [
                '{"type":"user","uuid":"u1","timestamp":"2026-09-07T10:00:00.000Z",'
                '"isSidechain":false,"message":{"role":"user","content":"fan out"}}',
                '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
                '"timestamp":"2026-09-07T10:00:01.000Z","message":{"id":"m1","role":"assistant",'
                '"stop_reason":"tool_use","content":[{"type":"tool_use","id":"tu1","name":"Agent",'
                '"input":{"description":"check the tree","subagent_type":"Explore"}}]}}',
                '{"type":"user","uuid":"u2","timestamp":"2026-09-07T10:00:04.000Z",'
                '"isSidechain":false,"message":{"role":"user","content":['
                '{"type":"tool_result","tool_use_id":"tu1","content":"clean"}]}}',
                '{"type":"assistant","uuid":"a2","requestId":"r2","isSidechain":false,'
                '"timestamp":"2026-09-07T10:00:05.000Z","message":{"id":"m2","role":"assistant",'
                '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    sub_dir = folder / sid / "subagents"
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / "agent-agent01.jsonl").write_text(
        "\n".join(
            [
                '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"agent01",'
                '"timestamp":"2026-09-07T10:00:02.000Z","message":{"id":"sm1",'
                '"role":"assistant","model":"m-1","stop_reason":"tool_use","content":['
                '{"type":"tool_use","id":"tu2","name":"Bash",'
                '"input":{"command":"git status --short"}}]}}',
                '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"agent01",'
                '"timestamp":"2026-09-07T10:00:03.000Z","message":{"role":"user","content":['
                '{"type":"tool_result","tool_use_id":"tu2","content":""}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (sub_dir / "agent-agent01.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "check the tree", "toolUseId": "tu1"}),
        encoding="utf-8",
    )

    manifests = TraceInstrumentor(TelemetryConfig(enabled=True), sink=None).plan(
        cwd, sid, ClaudeCodeAdapter()
    )
    shell = [
        span
        for manifest in manifests
        for span in manifest.spans
        if span.attributes.get(ShellAttr.CATEGORY) == SHELL_CATEGORY
    ]

    assert len(shell) == 1
    (call,) = shell
    payload = json.loads(str(call.attributes[LangfuseAttr.OBSERVATION_INPUT]))
    assert payload[INPUT_COMMAND_PATH] == "git status --short"
    # A clean `git status --short` prints nothing, and that is a RESULT.
    assert call.attributes[ShellAttr.RESULT] == ShellResult.EMPTY
    # The delegated call says whose it is, so a cohort can be scoped to — or
    # away from — sub-agent work without walking the tree.
    assert call.attributes["grove.agent.depth"] == 1
    assert call.attributes["grove.agent.parent.id"] == "agent01"
    # The spawning `Agent` call is not a shell call and gets no category.
    spawn = next(
        span
        for manifest in manifests
        for span in manifest.spans
        if span.attributes.get(GenAiAttr.TOOL_NAME) == "Agent"
    )
    assert ShellAttr.CATEGORY not in spawn.attributes


# ─── the composition seam itself ─────────────────────────────────────────────


def test_a_call_with_no_readable_command_still_reports_that_a_shell_ran() -> None:
    observation = ShellObservation(
        call=None,
        source=ToolSource.TRANSCRIPT,
        agent_kind="codex",
        tool_name="exec_command",
        resolved=True,
        output="something",
    )
    attributes = observation.span_attributes()
    assert attributes[ShellAttr.CATEGORY] == SHELL_CATEGORY
    assert LangfuseAttr.OBSERVATION_INPUT not in attributes
    assert attributes[ShellAttr.RESULT] == ShellResult.CAPTURED
