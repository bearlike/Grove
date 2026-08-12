"""Shell-command time attribution: the parser, the projection, the ranking.

Every parser case here is a SHAPE taken from the real on-host corpora, not an
invented one — the module docstring in `_command.py` records the measurements
these pin. The three that matter most and would be silently wrong without a
test are: an operator inside a quoted span, a heredoc BODY full of `|`, and a
prefix command's own flags (`xargs -I{}`, `env -u VAR`) being mistaken for the
executable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents import AgentActivity, AgentActivityState, AgentMessage, ContentBlock
from grove.core.agents.model import SessionRef
from grove.core.agents.registry import all_adapters
from grove.core.config import GroveConfig, UsageCommandsConfig
from grove.core.contracts.usage import UsageFilters
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.usage._command import SHELL_TOOL_NAMES, LeadingCommand
from grove.core.usage._schema import SCHEMA_VERSION
from grove.core.usage._store import UsageStore
from grove.core.usage.insights import ERROR_REPORTING_PROVIDERS, BashCommandRanking
from grove.core.usage.projector import UsageProjector


@pytest.fixture
def leading() -> LeadingCommand:
    return LeadingCommand(UsageCommandsConfig())


@dataclass
class _ShellAdapter:
    """The adapter seam, answering with one session's normalized messages."""

    ref: SessionRef
    paths: list[Path]
    messages: tuple[AgentMessage, ...]
    kind: str = "claude_code"
    activity: AgentActivity = field(
        default_factory=lambda: AgentActivity(state=AgentActivityState.WAITING)
    )

    def discover_all(self) -> tuple[SessionRef, ...]:
        return (self.ref,)

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        del cwd, session_id
        return self.paths

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        del cwd, session_id
        return self.messages

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        del cwd, session_id
        return self.activity


# ---------------------------------------------------------------------------
# The parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # The plain case, and a path.
        ("git status --short", "git"),
        ("/usr/bin/git status", "git"),
        ("./gradlew assembleDebug", "gradlew"),
        # A pipeline is credited to its LEADING stage — the rule the 3.05x
        # inflation measurement settled. `xargs pylint` is deliberately not it.
        ("find . -name '*.py' | xargs -I{} pylint {}", "find"),
        ("grep -rn foo src | head -20 | wc -l", "grep"),
        # A builtin spawns no process, so the next real command is billed.
        ("cd /repo && make -j4", "make"),
        ("echo hello | jq .", "jq"),
        ("[ -f x ] && pytest", "pytest"),
        ("[[ -d src ]] && ruff check src", "ruff"),
        # Assignments and prefix commands are not executables.
        ("VAR=1 pytest -q", "pytest"),
        ("sudo -u bob systemctl restart grove-daemon", "systemctl"),
        ("env -u CLAUDE_CONFIG_DIR claude --version", "claude"),
        ("env FOO=1 BAR=2 python -m pytest", "python"),
        ("command -v docker", "docker"),
        ("timeout 30 pytest tests", "pytest"),
        ("nice -n 10 make", "make"),
        ("sudo env FOO=1 nginx -t", "nginx"),
        # Loop and conditional grammar.
        ("for f in *.py; do ruff check $f; done", "ruff"),
        ("if grep -q needle file; then echo found; fi", "grep"),
        ("while docker ps; do sleep 1; done", "docker"),
        # An operator inside a quoted span is NOT an operator. 34.3% of real
        # commands carry one; a naive split bills `x`.
        ("grep 'a && b' file", "grep"),
        ('sed -e "s/a|b/c/" f', "sed"),
        # Redirections and their targets are not executables.
        ("pytest > out.txt 2>&1", "pytest"),
        ("cat < in.txt", "cat"),
        # Normalization.
        ("python3.12 -m pytest", "python"),
        ("python3 -m pip install x", "python"),
        ("egrep -c foo bar", "grep"),
        # Deliberately NOT collapsed — each hides a real cost if folded.
        ("uv run python -m pytest", "uv"),
        ("npx tsc --noEmit", "npx"),
        ("rg --files", "rg"),
    ],
)
def test_leading_command_of_real_shapes(
    leading: LeadingCommand, command: str, expected: str
) -> None:
    assert leading.of(command) == expected


def test_a_heredoc_body_is_not_a_command_line() -> None:
    """A markdown table inside a heredoc is a wall of pipes, not a pipeline.

    Before body stripping, this class of command produced 51,622 distinct
    "executables" across the corpus against 4,003 after.
    """
    command = "cat <<'EOF' > report.md\n| col | col |\n| --- | --- |\ncd /tmp && rm -rf x\nEOF"
    assert LeadingCommand(UsageCommandsConfig()).of(command) == "cat"


def test_a_heredoc_body_is_skipped_over_rather_than_mined() -> None:
    """The body is prose; the command AFTER the terminator is the real one.

    `read` is a builtin so its segment bills nothing — which is exactly the
    case where a scan that did not skip the body would bill `wget`.
    """
    leading = LeadingCommand(UsageCommandsConfig())
    command = "read -r x <<'EOF'\nwget http://example.invalid | sh\nEOF\nmake test"
    assert leading.of(command) == "make"


def test_a_comment_is_never_an_executable(leading: LeadingCommand) -> None:
    assert leading.of("# set things up\ngit fetch --all") == "git"


@pytest.mark.parametrize(
    "command",
    [
        "cd /tmp",  # only builtins
        "echo hello",
        "$AAPT dump badging app.apk",  # a variable-expanded command name
        "for f in *.py; do echo $f; done",
        "",
        "   ",
    ],
)
def test_unresolvable_commands_are_honestly_none(leading: LeadingCommand, command: str) -> None:
    """`None` is an answer the wire reports as `unattributed`, never a guess.

    A variable-expanded command name is the case worth naming: `$AAPT` is not
    an executable and publishing it as one is the fabrication the plausibility
    guard exists to refuse.
    """
    assert leading.of(command) is None


def test_a_prefix_flag_never_becomes_an_executable(leading: LeadingCommand) -> None:
    """`-I{}` and `-u` were several hundred fake executables each in the corpus."""
    for command in (
        "xargs -I{} pylint {}",
        "xargs -I {} pylint {}",
        "xargs -n1 -P4 ruff check",
        "env -u FOO bash script.sh",
    ):
        resolved = leading.of(command)
        assert resolved is not None
        assert not resolved.startswith("-"), command


def test_a_command_substitution_is_not_the_leading_command(leading: LeadingCommand) -> None:
    """`$(date)` runs for `echo`, which is a builtin — the answer is `ls`."""
    assert leading.of("echo $(date) && ls -la") == "ls"


def test_the_fallback_alone_still_answers_the_common_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lean install with no bashlex degrades rather than failing to import.

    The fallback is ~79% correct against real commands where bashlex is ~100%,
    so this pins that the degrade is a degrade and not a blackout.
    """
    monkeypatch.setattr("grove.core.usage._command.bashlex", None)
    leading = LeadingCommand(UsageCommandsConfig())
    assert leading.of("git status") == "git"
    assert leading.of("cd /repo && make") == "make"
    assert leading.of("find . | xargs -I{} pylint {}") == "find"


def test_the_bashlex_budget_is_enforced_rather_than_merely_passed(
    monkeypatch: pytest.MonkeyPatch, leading: LeadingCommand
) -> None:
    """A parse that runs long is ABANDONED, and the fallback still answers.

    Patched to zero budget so the watchdog fires deterministically; a test that
    asserted the constant is passed could not tell an enforced bound from an
    ignored one.
    """
    monkeypatch.setattr(LeadingCommand, "PARSE_BUDGET_MS", 0)
    assert leading.of("git status --short | head") == "git"


def test_an_over_long_command_skips_bashlex_entirely(leading: LeadingCommand) -> None:
    command = "git commit -m '" + "x" * (LeadingCommand.MAX_LENGTH + 10) + "'"
    assert leading.of(command) == "git"


def test_background_and_command_extraction_read_both_harness_shapes() -> None:
    assert LeadingCommand.command_text({"command": "ls -la"}) == "ls -la"
    assert LeadingCommand.command_text({"cmd": "git status"}) == "git status"
    assert LeadingCommand.command_text({"command": ["bash", "-lc", "ls"]}) == "bash -lc ls"
    assert LeadingCommand.command_text({"file_path": "/x"}) is None
    assert LeadingCommand.command_text(None) is None
    assert LeadingCommand.in_background({"command": "sleep 100", "run_in_background": True})
    assert not LeadingCommand.in_background({"command": "sleep 100"})


def test_normalization_is_configuration_not_a_table() -> None:
    cfg = UsageCommandsConfig(basename=False, version_suffix_pattern="(?!)", aliases={"rg": "grep"})
    leading = LeadingCommand(cfg)
    assert leading.of("/usr/bin/python3.12 -m pytest") == "/usr/bin/python3.12"
    assert leading.of("rg --files") == "grep"


def test_the_version_pattern_keeps_a_digit_that_is_part_of_the_name(
    leading: LeadingCommand,
) -> None:
    """`base64` and `bzip2` are names, not versions — a bare digit strip invents tools."""
    assert leading.of("base64 -d payload") == "base64"
    assert leading.of("bzip2 -d archive.bz2") == "bzip2"
    assert leading.of("sha256sum file") == "sha256sum"


def test_a_bad_version_pattern_is_refused_at_config_load() -> None:
    with pytest.raises(Exception, match="version_suffix_pattern"):
        GroveConfig.model_validate({"usage": {"commands": {"version_suffix_pattern": "([a-z"}}})


# ---------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------


def test_the_schema_version_moved_so_an_existing_cache_rebuilds() -> None:
    """Version 4 adds no column, only meaning — without the bump every already
    indexed row keeps a NULL `target` and the card reads as a quiet week."""
    assert SCHEMA_VERSION >= 4


def test_the_projector_fills_target_for_a_shell_call_and_flags_the_background_one(
    tmp_path: Path,
) -> None:
    """The whole feature end to end at the write seam.

    Pinned through a real `UsageProjector` rather than by calling the parser,
    because the value has to survive the `tool_use` → `tool_result` hop: the
    duration lives on the result row and the attribution on the call.
    """
    profile = tmp_path / "claude_code-profile"
    transcript = profile / "projects" / "project" / "session-a.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    started = datetime(2026, 8, 9, 12, tzinfo=UTC)

    def call(tool_use_id: str, command: str, *, background: bool) -> tuple[AgentMessage, ...]:
        tool_input: dict[str, object] = {"command": command}
        if background:
            tool_input["run_in_background"] = True
        return (
            AgentMessage(
                role="assistant",
                content=(
                    ContentBlock(
                        type="tool_use",
                        tool_name="Bash",
                        tool_use_id=tool_use_id,
                        tool_input=tool_input,
                    ),
                ),
                timestamp=started,
            ),
            AgentMessage(
                role="tool",
                content=(ContentBlock(type="tool_result", tool_use_id=tool_use_id, text="ok"),),
                timestamp=started + timedelta(seconds=3),
            ),
        )

    messages = (
        *call("call-1", "cd /repo && uv run pytest -q | tail -20", background=False),
        *call("call-2", "npm run dev", background=True),
    )
    ref = SessionRef(
        session_id="session-a",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=transcript,
        birth=started,
        mtime=transcript.stat().st_mtime,
    )
    adapter = _ShellAdapter(ref=ref, paths=[transcript], messages=messages)
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json"))
    UsageProjector(cfg=cfg, registry=registry, store=store, adapters=(adapter,)).refresh()

    rows = store.query(
        "SELECT kind, target, duration_ms, attrs_json FROM usage_events "
        "WHERE tool_name='Bash' ORDER BY seq"
    )
    assert [(row["kind"], row["target"]) for row in rows] == [
        ("tool_call", "uv"),
        ("tool_result", "uv"),
        ("tool_call", "npm"),
        ("tool_result", "npm"),
    ]
    assert rows[1]["duration_ms"] == 3000
    assert rows[1]["attrs_json"] is None
    assert json.loads(rows[3]["attrs_json"]) == {"background": True}

    view = BashCommandRanking(store=store, cfg=cfg).view(UsageFilters())
    assert {command.executable for command in view.commands} == {"uv", "npm"}
    assert next(c for c in view.commands if c.executable == "npm").background_calls == 1
    store.close()


def test_shell_tool_names_are_shared_by_the_writer_and_the_reader() -> None:
    assert "Bash" in SHELL_TOOL_NAMES
    assert "exec_command" in SHELL_TOOL_NAMES
    # A background poll addresses a job by id and carries no command; counting
    # it would inflate `unattributed` with calls that never had a command.
    assert "BashOutput" not in SHELL_TOOL_NAMES


# ---------------------------------------------------------------------------
# The ranking
# ---------------------------------------------------------------------------


def _epoch(offset: int = 0) -> int:
    return int(datetime(2026, 8, 9, 12, tzinfo=UTC).timestamp()) + offset


def _seeded_store(tmp_path: Path, rows: list[tuple[str | None, int, bool, str]]) -> UsageStore:
    """One session plus one `tool_result` row per `(target, ms, background, tool)`."""
    store = UsageStore(tmp_path / "usage.db")
    with store.write() as conn:
        conn.execute(
            "INSERT INTO sources(source_id, provider, root, label) "
            "VALUES('source', 'claude_code', '/profile', 'profile')"
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, cwd, project, account_id, "
            "started_at, last_event_at, cost_provenance, parser_health) "
            "VALUES('s1', 'source', 'claude_code', '/repo', '/repo', 'acct', ?, ?, "
            "'unknown', 'ok')",
            (_epoch(), _epoch(1000)),
        )
        for seq, (target, duration_ms, background, tool) in enumerate(rows, start=1):
            conn.execute(
                "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, tool_name, "
                "target, duration_ms, duration_source, attrs_json) "
                "VALUES('s1', 'source', ?, ?, 'tool_result', ?, ?, ?, 'derived', ?)",
                (
                    seq,
                    _epoch(seq),
                    tool,
                    target,
                    duration_ms,
                    json.dumps({"background": True}) if background else None,
                ),
            )
    return store


def test_the_ranking_orders_by_attributed_time_and_reports_its_denominator(
    tmp_path: Path,
) -> None:
    store = _seeded_store(
        tmp_path,
        [
            ("pytest", 40_000, False, "Bash"),
            ("pytest", 20_000, False, "Bash"),
            ("git", 1_000, False, "Bash"),
            ("uv", 5_000, False, "exec_command"),
        ],
    )
    view = BashCommandRanking(store=store, cfg=GroveConfig()).view(UsageFilters())
    assert [command.executable for command in view.commands] == ["pytest", "uv", "git"]
    assert view.commands[0].calls == 2
    assert view.commands[0].total_ms == 60_000
    assert view.commands[0].avg_ms == 30_000
    assert view.total_calls == 4
    assert view.total_ms == 66_000
    assert view.unattributed_calls == 0


def test_unattributed_calls_are_reported_rather_than_dropped(tmp_path: Path) -> None:
    """A resolver that returns nothing must be VISIBLE, or the ranked rows read
    as the whole population."""
    store = _seeded_store(
        tmp_path,
        [("pytest", 10_000, False, "Bash"), (None, 7_000, False, "Bash")],
    )
    view = BashCommandRanking(store=store, cfg=GroveConfig()).view(UsageFilters())
    assert [command.executable for command in view.commands] == ["pytest"]
    assert view.unattributed_calls == 1
    assert view.unattributed_ms == 7_000
    assert view.total_calls == 2
    assert view.total_ms == 17_000


def test_censored_and_background_calls_are_counted_separately(tmp_path: Path) -> None:
    """Both are ways this ranking lies, so both must reach the wire per row.

    A duration at the harness's 600 s ceiling says when the tool gave up, and a
    backgrounded call returns a handle at once — biasing long work DOWNWARD.
    """
    store = _seeded_store(
        tmp_path,
        [
            ("pytest", 601_000, False, "Bash"),
            ("pytest", 600_000, False, "Bash"),
            ("pytest", 5_000, False, "Bash"),
            ("npm", 120, True, "Bash"),
        ],
    )
    view = BashCommandRanking(store=store, cfg=GroveConfig()).view(UsageFilters())
    by_name = {command.executable: command for command in view.commands}
    assert by_name["pytest"].censored_calls == 2
    assert by_name["pytest"].background_calls == 0
    assert by_name["npm"].background_calls == 1
    assert by_name["npm"].censored_calls == 0


def test_the_censor_threshold_is_configuration(tmp_path: Path) -> None:
    store = _seeded_store(tmp_path, [("pytest", 30_000, False, "Bash")])
    cfg = GroveConfig.model_validate({"usage": {"commands": {"censored_at_ms": 10_000}}})
    assert (
        BashCommandRanking(store=store, cfg=cfg).view(UsageFilters()).commands[0].censored_calls
        == 1
    )
    disabled = GroveConfig.model_validate({"usage": {"commands": {"censored_at_ms": 0}}})
    assert (
        BashCommandRanking(store=store, cfg=disabled)
        .view(UsageFilters())
        .commands[0]
        .censored_calls
        == 0
    )


def test_a_non_shell_tool_never_enters_the_ranking(tmp_path: Path) -> None:
    store = _seeded_store(
        tmp_path,
        [("pytest", 10_000, False, "Bash"), ("/repo/x.py", 900, False, "Edit")],
    )
    view = BashCommandRanking(store=store, cfg=GroveConfig()).view(UsageFilters())
    assert [command.executable for command in view.commands] == ["pytest"]
    assert view.total_calls == 1


def _seeded_mixed_store(tmp_path: Path, rows: list[tuple[str, str, bool]]) -> UsageStore:
    """One session per provider, plus one `tool_result` per `(provider, target, errored)`.

    The ranking's error column joins `usage_events` to `sessions` for the
    provider, so a fixture that seeds only one provider can never see the bug
    this exists for.
    """
    store = UsageStore(tmp_path / "usage.db")
    with store.write() as conn:
        for provider in ("claude_code", "codex"):
            conn.execute(
                "INSERT INTO sources(source_id, provider, root, label) VALUES(?, ?, ?, ?)",
                (f"src-{provider}", provider, f"/profile/{provider}", provider),
            )
            conn.execute(
                "INSERT INTO sessions(session_id, source_id, provider, cwd, project, "
                "account_id, started_at, last_event_at, cost_provenance, parser_health) "
                "VALUES(?, ?, ?, '/repo', '/repo', 'acct', ?, ?, 'unknown', 'ok')",
                (f"s-{provider}", f"src-{provider}", provider, _epoch(), _epoch(1000)),
            )
        for seq, (provider, target, errored) in enumerate(rows, start=1):
            conn.execute(
                "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, tool_name, "
                "target, duration_ms, duration_source, is_error) "
                "VALUES(?, ?, ?, ?, 'tool_result', ?, ?, 1000, 'derived', ?)",
                (
                    f"s-{provider}",
                    f"src-{provider}",
                    seq,
                    _epoch(seq),
                    "Bash" if provider == "claude_code" else "exec_command",
                    target,
                    int(errored),
                ),
            )
    return store


def test_the_error_denominator_excludes_providers_that_cannot_report_one(
    tmp_path: Path,
) -> None:
    """The measured defect: Codex records no structural tool-error flag, so its
    calls inflate `calls` and can only ever add 0 to `error_calls`.

    `npm` here is the shipped bug's shape — 2 real failures over 4 measurable
    Claude calls is 50%, but published against all 8 calls it read 25%, and the
    deflation differs per row because the provider mix does.
    """
    store = _seeded_mixed_store(
        tmp_path,
        [
            *[("claude_code", "npm", index < 2) for index in range(4)],
            *[("codex", "npm", False) for _ in range(4)],
            *[("claude_code", "grep", False) for _ in range(3)],
        ],
    )
    by_name = {
        command.executable: command
        for command in BashCommandRanking(store=store, cfg=GroveConfig())
        .view(UsageFilters())
        .commands
    }
    assert by_name["npm"].calls == 8
    assert by_name["npm"].error_calls == 2
    assert by_name["npm"].error_reportable_calls == 4
    # A row with no silent calls keeps calls == reportable, so the honest
    # denominator is not a behaviour change for a single-provider scope.
    assert by_name["grep"].calls == by_name["grep"].error_reportable_calls == 3
    assert by_name["grep"].error_calls == 0
    store.close()


def test_a_row_whose_whole_population_is_silent_reports_zero_reportable_calls(
    tmp_path: Path,
) -> None:
    """The case a client must render as *not measured*, never as a confident 0%:
    zero errors over zero measurable calls is not evidence that nothing failed."""
    store = _seeded_mixed_store(tmp_path, [("codex", "pytest", False) for _ in range(5)])
    command = BashCommandRanking(store=store, cfg=GroveConfig()).view(UsageFilters()).commands[0]
    assert command.calls == 5
    assert command.error_calls == 0
    assert command.error_reportable_calls == 0
    store.close()


def test_the_error_reporting_providers_come_from_the_adapters(tmp_path: Path) -> None:
    """The capability is the ADAPTER's to declare — a provider-name list in this
    module's SQL would be policy in code, correct only until an adapter changes.

    Pinned against the adapters themselves rather than a literal, so flipping
    `reports_tool_errors` moves the audit with it. The other half is a census:
    every registered adapter has answered the question, so a new adapter cannot
    join the roster silently un-declared.
    """
    del tmp_path
    assert (
        tuple(sorted(a.kind for a in all_adapters() if a.reports_tool_errors))
        == ERROR_REPORTING_PROVIDERS
    )
    assert all(isinstance(adapter.reports_tool_errors, bool) for adapter in all_adapters())
    # Codex is the reason this exists: 31,373 real `function_call_output`
    # records carry exactly {type, call_id, output} and no error-shaped key.
    assert "codex" not in ERROR_REPORTING_PROVIDERS
    assert "claude_code" in ERROR_REPORTING_PROVIDERS


def test_the_ranked_list_is_capped_but_the_totals_are_not(tmp_path: Path) -> None:
    rows: list[tuple[str | None, int, bool, str]] = [
        (f"tool{index}", (index + 1) * 1_000, False, "Bash") for index in range(8)
    ]
    store = _seeded_store(tmp_path, rows)
    cfg = GroveConfig.model_validate({"usage": {"max_breakdown_rows": 3}})
    view = BashCommandRanking(store=store, cfg=cfg).view(UsageFilters())
    assert len(view.commands) == 3
    assert view.total_calls == 8
    assert view.total_ms == sum(row[1] for row in rows)
