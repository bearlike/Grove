"""Codex content traces: the promoted spine, the kind-agnostic forward, the gate.

The claim under test is that Grove needs no Codex-specific tracing code — the
adapter already returns the same normalized ``AgentMessage`` spine, so promoting
``read_messages`` onto the shared ``AgentAdapter`` Protocol is the whole wiring.
So this drives the REAL ``CodexAdapter`` against a REAL rollout shape (the
adapter suite's sanitized on-host fixture, re-homed at the workspace's cwd —
never a hand-built JSONL, which is how every Codex parsing trap has hidden) and
the REAL ``opentelemetry-sdk`` through an in-memory exporter.

It also pins the half the mechanism must keep supporting both ways: a deployment
that has adopted the Langfuse Codex plugin leaves ``content_owner`` unset and
gets context only; one that has not names ``{"codex": "grove"}`` and gets the
words. Grove ships neither as a default.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from grove.core.activity import DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession, all_adapters
from grove.core.config import GroveConfig, TelemetryConfig
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.trace import SpanSink, sink_from_processor
from grove.core.trace_forwarder import TraceForwarder
from grove.core.workspace import WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 4, 28, 20, 0, 0, tzinfo=UTC)
ROLLOUT = Path(__file__).parent / "agents" / "fixtures" / "codex_basic.jsonl"
CODEX_SID = "019dd5d5-60fb-7461-bd07-b6e8cf342726"

# ─── builders ────────────────────────────────────────────────────────────────


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    path = tmp_path / "proj" / ".worktrees" / "add-healthcheck"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandboxed Codex store — ``CODEX_HOME`` and ``Path.home`` both inside
    ``tmp_path``, so nothing reaches the developer's real ``~/.codex``."""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


@pytest.fixture
def registry(tmp_path: Path) -> RepoRegistry:
    return RepoRegistry(
        cfg=GroveConfig.model_validate({}), store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


@pytest.fixture
def exported() -> tuple[InMemorySpanExporter, SpanSink]:
    exporter = InMemorySpanExporter()
    return exporter, sink_from_processor(SimpleSpanProcessor(exporter), Resource.create({}))


def _install_rollout(codex_home: Path, cwd: Path) -> None:
    """Re-home the adapter suite's real rollout slice at ``cwd``.

    Codex records the cwd ONLY in ``session_meta`` and ``turn_context`` (never
    per line), and ``locate`` confirms a session by that recorded value — so
    re-pointing exactly those two fields is what makes a real on-host shape
    resolve as this workspace's session, with every other record untouched.
    """
    lines = ROLLOUT.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line]
    for record in records:
        payload = record.get("payload")
        if isinstance(payload, dict) and "cwd" in payload:
            payload["cwd"] = str(cwd)
    # The shared adapter fixture deliberately ends on a tool result even though
    # its status stream says task_complete. Immutable telemetry cannot use that
    # event-only signal without teaching a second transcript parser, so close
    # this tracing fixture with the normalized final response a safe replay
    # requires.
    records.insert(
        -1,
        {
            "timestamp": "2026-04-28T20:51:44.708Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
                "phase": "final_answer",
            },
        },
    )
    target = codex_home / "sessions" / "2026" / "04" / "28"
    target.mkdir(parents=True)
    (target / f"rollout-2026-04-28T13-43-44-{CODEX_SID}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


def _state(worktree: Path) -> WorkspaceState:
    return WorkspaceState(
        id="ws-codex",
        title="add-healthcheck",
        repo_root=str(worktree.parent.parent),
        branch="grove/add-healthcheck",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session="grove-add-healthcheck",
        agent_name="codex",
        agent_kind="codex",
        status=WorkspaceStatus.RUNNING,
        created_at=T0,
        updated_at=T0,
    )


def _delta(state: WorkspaceState) -> DashboardDelta:
    session = SessionActivity(
        session=AgentSession(
            session_id=CODEX_SID,
            transcript_path=None,
            adapter_kind="codex",
            # Codex mints nothing at launch, so its sessions always reach Grove
            # through discovery — the provenance a real Codex row carries.
            provenance="fs_discovered",
        ),
        activity=AgentActivity(state=AgentActivityState.WORKING),
    )
    row = WorkspaceActivity(
        state=state,
        sessions=(session,),
        base_ahead=0,
        base_behind=0,
        diff_added=0,
        diff_removed=0,
        dirty_files=0,
        pane_target=None,
        recent_commits=(),
        observed_at=T0,
    )
    return DashboardDelta(
        kind="session_activity",
        seq=1,
        workspace_id=state.id,
        repo_root=state.repo_root,
        workspace=row,
    )


def _run(forwarder: TraceForwarder, state: WorkspaceState) -> None:
    for job in forwarder.evaluate(_delta(state)):
        forwarder.forward(job)


def _names(exporter: InMemorySpanExporter) -> list[str]:
    return [span.name for span in exporter.get_finished_spans()]


def _attributes_of_kind(exporter: InMemorySpanExporter, key: str, kind: str) -> list[str]:
    """:func:`_attributes`, narrowed to one observation type.

    Needed where a fact legitimately appears on two KINDS of span — a turn's
    task rides both its root ``agent`` span and the ``generation`` that consumed
    it — so a whole-export count can no longer distinguish "one message rendered
    twice" from "the same words arrived from two sources".
    """
    return [
        str(span.attributes[key])
        for span in exporter.get_finished_spans()
        if span.attributes
        and key in span.attributes
        and span.attributes.get("langfuse.observation.type") == kind
    ]


def _attributes(exporter: InMemorySpanExporter, key: str) -> list[str]:
    """Every span's value for one attribute, as text.

    Read across the whole export rather than per named span because Codex writes
    one rollout record per content block and the spine keeps them separate, so a
    turn's prompt, its reply and its tool call land on THREE sibling generations
    — the content is all there, but which span holds which half is the adapter's
    record shape, not a contract this suite should freeze.
    """
    return [
        str(span.attributes[key])
        for span in exporter.get_finished_spans()
        if span.attributes is not None and key in span.attributes
    ]


def _by_name(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    matches = [span for span in exporter.get_finished_spans() if span.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r}, got {len(matches)}"
    return matches[0]


# ─── the wiring claim: no Codex-specific tracing code ────────────────────────


def test_codex_content_owner_replays_prompts_replies_and_tool_payloads(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    codex_home: Path,
) -> None:
    """The acceptance criterion: a Codex session under Grove yields a trace with
    real prompts, assistant messages and tool input/output — through the same
    forwarder and the same instrumentor Claude Code rides, with the adapter
    resolved by kind alone."""
    exporter, sink = exported
    _install_rollout(codex_home, worktree)
    state = _state(worktree)
    forwarder = TraceForwarder(
        cfg=TelemetryConfig(enabled=True, content_owner={"codex": "grove"}),
        registry=registry,
        sink=sink,
    )

    _run(forwarder, state)

    names = _names(exporter)
    # Span names are now composed per the OTel GenAI convention: "invoke_agent
    # {agent}", "chat {model}", "execute_tool {tool}" — never the bare subject.
    assert "invoke_agent grove:context" in names
    assert f"invoke_agent session:{CODEX_SID}" in names
    # The fixture's one `function_call` — its `function_call_output` is the
    # result side and must not become a second span.
    assert names.count("execute_tool exec_command") == 1
    # Every generation names the model that served it. Codex reports its model
    # on `turn_context` rather than per message, so the spine used to carry none
    # and these spans were the bare word `chat` — which also meant the cost
    # layer, which looks a rate up BY model, could never price them.
    assert names.count("chat gpt-5.5") == 4

    prompts = _attributes(exporter, "langfuse.observation.input")
    completions = _attributes(exporter, "langfuse.observation.output")
    assert "Add a healthcheck endpoint to the service" in prompts
    assert "I'll add the endpoint." in completions
    tool = _by_name(exporter, "execute_tool exec_command")
    assert tool.attributes is not None
    assert "sed -n '1,40p' app.py" in str(tool.attributes["langfuse.observation.input"])
    assert tool.attributes["langfuse.observation.output"] == "ok\nProcess exited with code 0"


def test_the_preamble_is_not_a_prompt_because_the_adapter_already_filtered_it(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    codex_home: Path,
) -> None:
    """Grove writes no Codex parsing here, and this is the evidence: the
    injected ``<environment_context>`` / AGENTS.md preamble and the ``event_msg``
    mirrors are absent from the trace because ``read_messages`` never emitted
    them — not because the tracer learned to recognize them."""
    exporter, sink = exported
    _install_rollout(codex_home, worktree)
    forwarder = TraceForwarder(
        cfg=TelemetryConfig(enabled=True, content_owner={"codex": "grove"}),
        registry=registry,
        sink=sink,
    )

    _run(forwarder, _state(worktree))

    prompts = _attributes(exporter, "langfuse.observation.input")
    assert not any("<environment_context>" in text for text in prompts)
    assert not any("AGENTS.md" in text for text in prompts)
    # The human prompt reaches the spine ONCE: the `event_msg/user_message`
    # mirror of the same words is not a `response_item`, so it never becomes a
    # message. Counted over GENERATIONS only, because the turn's root agent span
    # legitimately restates the same task as its own input — that is the turn
    # summary a reader opens first, and it is a second RENDERING of one message
    # rather than the second SOURCE this assertion exists to catch.
    generation_prompts = _attributes_of_kind(exporter, "langfuse.observation.input", "generation")
    assert generation_prompts.count("Add a healthcheck endpoint to the service") == 1
    generation_replies = _attributes_of_kind(exporter, "langfuse.observation.output", "generation")
    assert generation_replies.count("I'll add the endpoint.") == 1


def test_codex_emits_no_sub_agent_spans_because_it_records_no_threads(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    codex_home: Path,
) -> None:
    """The fleet walk stays gated on the Claude adapter's identity. Codex keeps
    no sub-agent transcripts at all, so the correct output is exactly one
    ``agent`` span (the session root) plus Grove's own context span."""
    exporter, sink = exported
    _install_rollout(codex_home, worktree)
    forwarder = TraceForwarder(
        cfg=TelemetryConfig(enabled=True, content_owner={"codex": "grove"}),
        registry=registry,
        sink=sink,
    )

    _run(forwarder, _state(worktree))

    agents = [
        span
        for span in exporter.get_finished_spans()
        if span.attributes is not None
        and span.attributes.get("langfuse.observation.type") == "agent"
    ]
    assert {span.name for span in agents} == {
        "invoke_agent grove:context",
        f"invoke_agent session:{CODEX_SID}",
    }


# ─── the recorded decision: both answers must stay reachable ─────────────────


def test_unset_codex_ownership_yields_context_only_for_a_plugin_deployment(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    codex_home: Path,
) -> None:
    """A deployment that HAS adopted the Langfuse Codex plugin leaves the kind
    unnamed, and Grove must then stay silent about content even with the rollout
    sitting right there, readable — the #459 rule, applied to the harness whose
    baseline emitter is optional."""
    exporter, sink = exported
    _install_rollout(codex_home, worktree)
    forwarder = TraceForwarder(cfg=TelemetryConfig(enabled=True), registry=registry, sink=sink)

    _run(forwarder, _state(worktree))

    assert _names(exporter) == ["invoke_agent grove:context"]


# ─── the promoted Protocol member ────────────────────────────────────────────


def test_every_registered_adapter_answers_the_spine(tmp_path: Path) -> None:
    """``read_messages`` is on the shared Protocol now, so the forwarder resolves
    an adapter by kind and reads it without a capability probe. An adapter with
    no message spine owes an honest ``()`` — never an ``AttributeError`` on a
    poll thread."""
    for adapter in all_adapters():
        assert adapter.read_messages(tmp_path, "nonexistent-session") == ()
