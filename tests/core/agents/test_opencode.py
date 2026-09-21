"""Coverage for the OpenCode SQLite-backed adapter.

``opencode_capture.db`` is a sanitized copy of real OpenCode 1.18.18 history.
It includes two sessions in one cwd, a full text/tool/todo conversation, two
empty sessions, and the observed failed-read shape: a tool part stuck at
``state.status == "running"`` without an output or an end time.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from grove.core.agents import AgentActivityState, opencode
from grove.core.agents.opencode import OpencodeAdapter

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURE = FIXTURES / "opencode_capture.db"
CWD = Path("/home/dev/projects/opencode-probe")
TOOL_SESSION = "ses_f525508a0ffeX8SampFgKxjEU7"
EMPTY_SESSION = "ses_f5255d97effeITSCQvW8ue24ns"
RUNNING_SESSION = "ses_f52496e67ffeGA8PTKiewfnNIu"


@pytest.fixture(autouse=True)
def _no_live_opencode_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every read on the database unless a test names a server itself.

    Clearing the address variable is what makes this true rather than merely
    likely: with none configured the adapter never constructs a client at all,
    so a developer box genuinely running OpenCode cannot change which source a
    test exercises — the second-network-reacher discipline `tests/CLAUDE.md`
    documents for the mewbo adapter. Tests proving the live-preference path
    patch the client methods explicitly."""
    monkeypatch.delenv(opencode._OpenCodeClient.BASE_URL_ENV, raising=False)
    monkeypatch.setattr(opencode._OpenCodeClient, "messages", lambda self, cwd, session_id: None)
    monkeypatch.setattr(opencode._OpenCodeClient, "descendants", lambda self, cwd, sid: None)
    monkeypatch.setattr(opencode._OpenCodeClient, "providers", lambda self, cwd: None)


def test_a_read_never_dials_a_server_nobody_configured(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No address means no HTTP at all, not a hopeful connection to a default.

    `opencode serve` takes `--port 0` and mints a per-launch password, so a
    fixed port can neither identify a Grove-owned server nor authenticate
    against one. The cost of trying lands on the ~1 Hz activity tick: an
    unreachable bounded GET measured 45 ms per call on this host, paid per
    session per read, for an answer the database already holds.
    """

    # The autouse fixture stubs the client's read METHODS, so patching those
    # again would pass whether or not a client is built. Refuse at construction
    # instead: that is the only point a guessed address can enter.
    def _refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an unconfigured read must never construct an HTTP client")

    monkeypatch.setattr(opencode._OpenCodeClient, "__init__", _refuse)

    assert adapter.parse_activity(CWD, TOOL_SESSION).state is not None
    assert adapter.read_turns(CWD, TOOL_SESSION)
    assert adapter.fleet_activity(CWD, TOOL_SESSION) == []
    assert adapter.available_models("opencode-missing-binary") == ()


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> OpencodeAdapter:
    data_home = tmp_path / "data"
    db = data_home / "opencode" / "opencode.db"
    db.parent.mkdir(parents=True)
    shutil.copyfile(CAPTURE, db)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    return OpencodeAdapter()


def test_discovers_only_sessions_in_requested_cwd(adapter: OpencodeAdapter) -> None:
    sessions = adapter.discover_sessions(CWD)

    assert TOOL_SESSION in sessions
    assert EMPTY_SESSION in sessions
    assert len(sessions) == 4
    assert all(summary.cwd == str(CWD) for summary in adapter.list_sessions(CWD))
    assert adapter.discover_sessions(Path("/home/dev/projects/homelab")) != sessions


def test_maps_real_tool_parts_and_todo(adapter: OpencodeAdapter) -> None:
    activity = adapter.parse_activity(CWD, TOOL_SESSION)
    turns = adapter.read_turns(CWD, TOOL_SESSION)
    messages = adapter.read_messages(CWD, TOOL_SESSION)
    todo = adapter.latest_todo(CWD, TOOL_SESSION)

    assert activity.state is AgentActivityState.WAITING
    assert activity.human_turns == 3
    assert activity.assistant_replies == 6
    assert activity.tool_calls == 4
    assert activity.model == "claude-sonnet-5"
    assert activity.tokens_in == 60_172
    assert activity.tokens_out == 355
    assert [
        block.tool_name
        for message in messages
        for block in message.content
        if block.type == "tool_use"
    ] == [
        "read",
        "bash",
        "todowrite",
        "bash",
    ]
    assert any(
        block.type == "tool_result"
        and block.tool_use_id == "toolu_01Ky38hM9gr4GpcF46kp4cDR"
        and block.text == "GROVE_PARITY_OK\n"
        for message in messages
        for block in message.content
    )
    assert todo is not None
    assert [(item.content, item.status) for item in todo.items] == [("parity", "pending")]
    tool_calls = [entry.tool for turn in turns for entry in turn.entries if entry.tool is not None]
    assert [call.status for call in tool_calls] == ["ok", "ok", "ok", "ok"]


def test_keeps_real_stuck_tool_running_without_inventing_an_error(adapter: OpencodeAdapter) -> None:
    """A real OpenCode 1.18.18 failed read remains a running part, not a result."""
    (turn,) = adapter.read_turns(CWD, RUNNING_SESSION)
    entry = next(
        entry for entry in turn.entries if entry.tool is not None and entry.tool.name == "read"
    )

    assert entry.tool is not None
    assert entry.tool.name == "read"
    assert entry.tool.status == "running"
    assert entry.tool.result is None
    assert entry.tool.duration_ms is None
    assert adapter.parse_activity(CWD, RUNNING_SESSION).state is AgentActivityState.WORKING


def test_empty_session_has_unknown_activity_and_no_messages(adapter: OpencodeAdapter) -> None:
    assert adapter.read_messages(CWD, EMPTY_SESSION) == ()
    assert adapter.read_turns(CWD, EMPTY_SESSION) == ()
    assert adapter.parse_activity(CWD, EMPTY_SESSION).state is AgentActivityState.UNKNOWN
    # Metadata resolves without a parse; the parse products are `None` at this
    # scope, which is "not parsed here" and NOT "no activity".
    metadata = adapter.session_summary(CWD, EMPTY_SESSION)
    assert metadata is not None
    assert metadata.title is not None
    assert metadata.activity is None

    # A caller that RENDERS activity asks for it, and then an empty session
    # reports UNKNOWN rather than a fabricated idle state.
    summary = adapter.session_summary(CWD, EMPTY_SESSION, full=True)
    assert summary is not None
    assert summary.title is not None
    assert summary.activity is not None
    assert summary.activity.state is AgentActivityState.UNKNOWN


def test_available_models_reads_the_configured_binary_and_keeps_provider_ids(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenCode prints its launch vocabulary, one exact provider/model id per line."""

    observed: list[tuple[str, ...]] = []

    def probe(argv: tuple[str, ...]) -> str | None:
        observed.append(argv)
        return "anthropic/claude-sonnet-5\ncustom/my-private-id[1m]\n"

    monkeypatch.setattr(opencode, "_probe_opencode_models", probe)

    assert adapter.available_models("/opt/custom-opencode --pure") == (
        "anthropic/claude-sonnet-5",
        "custom/my-private-id[1m]",
    )
    # The tool's LAUNCH flags are dropped: they configure an interactive
    # session and a subcommand is not one. `opencode --auto models` reads
    # `models` as the positional directory and fails outright.
    assert observed == [("/opt/custom-opencode", "models")]


def test_a_terminal_profiles_launch_flag_never_reaches_the_subcommand(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`opencode --auto` is a terminal profile; `--auto models` is a broken probe.

    `--auto` is boolean, so it does not consume the word — but `opencode`
    takes a positional DIRECTORY, so `models` lands there instead. Measured:
    "Failed to change directory to /tmp/models", exit 0, no output, and an
    empty catalog that renders as a disabled picker. Reordering does not help
    either; the flag is not valid on the subcommand at all.
    """
    observed: list[tuple[str, ...]] = []

    def probe(argv: tuple[str, ...]) -> str | None:
        observed.append(argv)
        # A faithful double: the real CLI treats a trailing word as a path,
        # so anything after `models` means this is not a catalog read.
        return "litellm/gpt-6-astra\n" if argv[-1] == "models" else None

    monkeypatch.setattr(opencode, "_probe_opencode_models", probe)
    monkeypatch.setattr(opencode._OpenCodeClient, "for_reads", staticmethod(lambda: None))

    terminal = "ssm-cli run --project opencode --config hurricane -- opencode --auto"
    assert adapter.available_models(terminal) == ("litellm/gpt-6-astra",)
    # The wrapper's own arguments survive; the tool's launch flag does not.
    assert observed == [
        (
            "ssm-cli",
            "run",
            "--project",
            "opencode",
            "--config",
            "hurricane",
            "--",
            "opencode",
            "models",
        )
    ]


def test_the_model_probe_budget_allows_for_a_credential_fetch() -> None:
    """The probe timeout is sized for a WRAPPED command, not a bare binary.

    `opencode models` alone is near-instant, so 2.0s looked generous — and a
    gateway profile runs it through a credential injector that fetches secrets
    over the network first. Measured 5.03s on a real host, so the probe timed
    out and the catalog came back empty on exactly the profiles that need it.
    Nothing surfaced the timeout: an empty catalog renders as a disabled
    picker, which is what a provider with no models renders as too.

    Pinned as a floor rather than an exact value so the bound can be retuned,
    but not silently dropped back under a real credential fetch.
    """
    assert opencode._MODEL_PROBE_TIMEOUT_SECONDS >= 10.0


def test_available_models_probes_through_a_credential_wrapper(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wrapped command probes the WRAPPER, not its first token.

    A gateway profile execs the real binary after ``--``
    (``ssm-cli run … -- opencode``). Probing only the first shell token runs
    ``ssm-cli models`` — not a command — so discovery returned ``()`` and the
    model picker rendered DISABLED with nothing to choose, which is
    indistinguishable from a provider that enumerates nothing. Probing the
    whole command also inherits the very credentials the wrapper exists to
    supply, which a bare-binary probe could never have.
    """
    observed: list[tuple[str, ...]] = []

    def probe(argv: tuple[str, ...]) -> str | None:
        observed.append(argv)
        # Only the real binary answers; the wrapper's own name does not.
        return "litellm/gpt-6-astra\n" if argv[-2:] == ("opencode", "models") else None

    monkeypatch.setattr(opencode, "_probe_opencode_models", probe)
    monkeypatch.setattr(opencode._OpenCodeClient, "for_reads", staticmethod(lambda: None))

    wrapped = "ssm-cli run --project opencode --config hurricane -- opencode"
    assert adapter.available_models(wrapped) == ("litellm/gpt-6-astra",)
    assert observed == [
        (
            "ssm-cli",
            "run",
            "--project",
            "opencode",
            "--config",
            "hurricane",
            "--",
            "opencode",
            "models",
        )
    ]


def test_available_models_uses_native_provider_catalog_only_when_cli_unavailable(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The running server is a fallback, never a replacement for CLI discovery."""

    monkeypatch.setenv(opencode._OpenCodeClient.BASE_URL_ENV, "http://127.0.0.1:4100")
    monkeypatch.setattr(opencode, "_probe_opencode_models", lambda argv: None)
    monkeypatch.setattr(
        opencode._OpenCodeClient,
        "providers",
        lambda self, cwd: {
            "all": [
                {"id": "edge", "models": {"custom-id": {}, "extra[1m]": {}}},
            ]
        },
    )

    assert adapter.available_models("opencode") == ("edge/custom-id", "edge/extra[1m]")


def test_live_messages_win_over_sqlite_and_carry_compaction(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live messages expose compaction before the server's SQLite projection catches up."""

    monkeypatch.setenv(opencode._OpenCodeClient.BASE_URL_ENV, "http://127.0.0.1:4100")
    monkeypatch.setattr(
        opencode._OpenCodeClient,
        "messages",
        lambda self, cwd, session_id: [
            {
                "info": {
                    "id": "msg-user",
                    "sessionID": session_id,
                    "role": "user",
                    "time": {"created": 1_789_620_000_000},
                },
                "parts": [{"type": "text", "text": "Live prompt"}],
            },
            {
                "info": {
                    "id": "msg-compact",
                    "sessionID": session_id,
                    "role": "compaction",
                    "time": {"created": 1_789_620_001_000},
                    "reason": "auto",
                    "summary": "Live summary",
                },
                "parts": [],
            },
        ],
    )

    messages = adapter.read_messages(CWD, TOOL_SESSION)

    assert [message.role for message in messages] == ["user", "compaction"]
    boundary = messages[-1].compaction
    assert boundary is not None
    assert boundary.trigger == "auto"
    assert boundary.summary == "Live summary"
    assert adapter.latest_task(CWD, TOOL_SESSION) == "Live prompt"


def test_sqlite_compaction_part_becomes_contentless_boundary(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The offline fallback carries OpenCode's real part shape through the shared spine."""

    monkeypatch.setattr(opencode._OpenCodeClient, "messages", lambda self, cwd, session_id: None)
    db = opencode._OpenCodeHome.database_path()
    with sqlite3.connect(db) as conn:
        message_id = "msg_compaction_capture"
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                message_id,
                TOOL_SESSION,
                1_789_620_200_000,
                1_789_620_200_000,
                json.dumps({"role": "assistant", "time": {"created": 1_789_620_200_000}}),
            ),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "prt_compaction_capture",
                message_id,
                TOOL_SESSION,
                1_789_620_200_000,
                1_789_620_200_000,
                json.dumps(
                    {
                        "type": "compaction",
                        "reason": "manual",
                        "summary": "Captured compact summary",
                    }
                ),
            ),
        )

    boundary = adapter.read_messages(CWD, TOOL_SESSION)[-1].compaction

    assert boundary is not None
    assert boundary.trigger == "manual"
    assert boundary.summary == "Captured compact summary"


def test_fleet_follows_parent_id_recursively_without_adopting_siblings(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only descendants of the requested root become fleet rows, even in one cwd."""

    monkeypatch.setattr(opencode._OpenCodeClient, "messages", lambda self, cwd, session_id: None)
    monkeypatch.setattr(opencode._OpenCodeClient, "descendants", lambda self, cwd, sid: None)
    db = opencode._OpenCodeHome.database_path()
    with sqlite3.connect(db) as conn:
        parent = TOOL_SESSION
        for child_id, parent_id, title in (
            ("ses_child", parent, "Child"),
            ("ses_grandchild", "ses_child", "Grandchild"),
            ("ses_sibling", None, "Same cwd but unrelated"),
        ):
            conn.execute(
                "INSERT INTO session (id, project_id, parent_id, slug, directory, title, "
                "version, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    child_id,
                    "project",
                    parent_id,
                    child_id,
                    str(CWD),
                    title,
                    "1.18.31",
                    1_789_620_300_000,
                    1_789_620_300_000,
                ),
            )

    fleet = adapter.fleet_activity(CWD, TOOL_SESSION)

    assert [session.session_id for session, _ in fleet] == ["ses_child", "ses_grandchild"]
    assert [session.parent_session_id for session, _ in fleet] == [TOOL_SESSION, "ses_child"]


def test_subagent_turns_use_child_session_messages_and_reject_unrelated_ids(
    adapter: OpencodeAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A child drill-in projects its own existing spine, never a second parser."""

    monkeypatch.setenv(opencode._OpenCodeClient.BASE_URL_ENV, "http://127.0.0.1:4100")

    child = "ses_child"
    live: dict[str, list[dict[str, Any]]] = {
        TOOL_SESSION: [],
        child: [
            {
                "info": {
                    "id": "msg-child-user",
                    "sessionID": child,
                    "role": "user",
                    "time": {"created": 1_789_620_400_000},
                },
                "parts": [{"type": "text", "text": "Child task"}],
            },
            {
                "info": {
                    "id": "msg-child-assistant",
                    "sessionID": child,
                    "role": "assistant",
                    "time": {"created": 1_789_620_401_000},
                },
                "parts": [{"type": "text", "text": "Child answer"}],
            },
        ],
    }
    monkeypatch.setattr(opencode._OpenCodeClient, "messages", lambda self, cwd, sid: live.get(sid))
    monkeypatch.setattr(
        opencode._OpenCodeClient,
        "descendants",
        lambda self, cwd, sid: {child} if sid == TOOL_SESSION else set(),
    )

    turns = adapter.subagent_turns(CWD, TOOL_SESSION, child)

    assert len(turns) == 1
    assert turns[0].user_text == "Child task"
    assert [entry.text for entry in turns[0].entries] == ["Child answer"]
    assert adapter.subagent_turns(CWD, TOOL_SESSION, "ses-unrelated") == ()
