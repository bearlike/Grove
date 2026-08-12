"""TraceForwarder: the content-ownership gate, the enriched context span, and
the no-repeated-export contract.

Drives the REAL `opentelemetry-sdk` through `sink_from_processor` (in-memory
exporter, never a network call) and the REAL `ClaudeCodeAdapter` against a
transcript on disk, so the gate is asserted on the spans that actually reach an
exporter rather than on a stubbed instrumentor. The only stubbed boundary is the
delta bus itself, which the broker's own suite already stubs the same way — the
forwarder depends on the bus SHAPE, so hand-built deltas of real engine IR mean
a contract drift fails to compile.

The decision half (`evaluate`) is pure and is called directly; `forward` is
called directly too, so every assertion is deterministic without a pool.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from grove.core.activity import DashboardDelta, SessionActivity, TodoProgress, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig, ModelPriceConfig, TelemetryConfig, UsagePricingConfig
from grove.core.contracts.tickets import TicketRef
from grove.core.phase import PhaseReport
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.telemetry.semconv import ObservationShapes
from grove.core.trace import SpanSink, derive_trace_id, sink_from_processor
from grove.core.trace_forwarder import TraceForwarder
from grove.core.workspace import WorkspaceState, WorkspaceStatus

T0 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC)
SESSION_ID = "24242424-2424-4242-8242-242424242424"
# `SpanRecord.{agent,generation,tool}` compose the rendered span name from
# `ObservationShapes`' `{operation} {subject}` rule, never from a literal span
# name — every call site (`trace_forwarder.py`'s enrichment marker AND
# `trace.py`'s replay spans) passes an identity string as that subject. Built
# from the SAME shared vocabulary production uses rather than a hand-spelled
# duplicate, so a convention change here fails to compile instead of drifting.
_agent_span_name = ObservationShapes.AGENT.span_name
_generation_span_name = ObservationShapes.GENERATION.span_name
_tool_span_name = ObservationShapes.TOOL.span_name

CONTEXT_SPAN_NAME = _agent_span_name("grove:context")

# ─── builders ────────────────────────────────────────────────────────────────


@pytest.fixture
def worktree(tmp_path: Path) -> Path:
    path = tmp_path / "proj" / ".worktrees" / "fix-auth"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


@pytest.fixture
def registry(tmp_path: Path) -> RepoRegistry:
    return RepoRegistry(
        cfg=GroveConfig.model_validate({}), store=JsonWorkspaceStore(path=tmp_path / "state.json")
    )


@pytest.fixture
def exported() -> tuple[InMemorySpanExporter, SpanSink]:
    exporter = InMemorySpanExporter()
    sink = sink_from_processor(SimpleSpanProcessor(exporter), Resource.create({}))
    return exporter, sink


def _forwarder(
    registry: RepoRegistry,
    sink: SpanSink,
    *,
    cfg: TelemetryConfig | None = None,
    pricing: UsagePricingConfig | None = None,
) -> TraceForwarder:
    return TraceForwarder(
        cfg=cfg if cfg is not None else TelemetryConfig(enabled=True),
        registry=registry,
        sink=sink,
        pricing=pricing,
    )


def _state(worktree: Path, **overrides: object) -> WorkspaceState:
    base = {
        "id": "ws1",
        "title": "fix-auth",
        "repo_root": str(worktree.parent.parent),
        "branch": "grove/fix-auth",
        "base_branch": "main",
        "worktree_path": str(worktree),
        "tmux_session": "grove-fix-auth",
        # A claude_code workspace is launched with `--session-id <minted>`, so
        # the record's minted id and the session the agent reports back are the
        # SAME value. Leaving this unset modelled a workspace that cannot exist
        # and hid the fact that the join key comes from the record, not the
        # discovered session.
        "agent_session_id": SESSION_ID,
        "agent_name": "claude",
        "agent_kind": "claude_code",
        "status": WorkspaceStatus.RUNNING,
        "created_at": T0,
        "updated_at": T0,
    }
    return WorkspaceState(**{**base, **overrides})  # type: ignore[arg-type]


def _session(
    *, kind: str = "claude_code", sid: str = SESSION_ID, parent_session_id: str | None = None
) -> SessionActivity:
    return SessionActivity(
        session=AgentSession(
            session_id=sid,
            transcript_path=None,
            adapter_kind=kind,
            provenance="grove_launched",
            parent_session_id=parent_session_id,
        ),
        activity=AgentActivity(state=AgentActivityState.WORKING),
    )


SUBAGENT_ID_1 = "a4abf4e870f8b3f58"
SUBAGENT_ID_2 = "aedfe590ae45bc1e5"
"""Real sub-agent thread ids from the #504 report: `a` + 16 hex — the same
shape #503 catalogues as `parentAgentId` values, never a canonical UUID."""


def _subagent_session(sid: str, *, parent: str = SESSION_ID) -> SessionActivity:
    return _session(sid=sid, parent_session_id=parent)


def _row(state: WorkspaceState, session: SessionActivity, **overrides: object) -> WorkspaceActivity:
    base = {
        "state": state,
        "sessions": (session,),
        "base_ahead": 0,
        "base_behind": 0,
        "diff_added": 0,
        "diff_removed": 0,
        "dirty_files": 0,
        "pane_target": None,
        "recent_commits": (),
        "observed_at": T0,
    }
    return WorkspaceActivity(**{**base, **overrides})  # type: ignore[arg-type]


def _delta(row: WorkspaceActivity) -> DashboardDelta:
    return DashboardDelta(
        kind="session_activity",
        seq=1,
        workspace_id=row.state.id,
        repo_root=row.state.repo_root,
        workspace=row,
    )


_TRANSCRIPT_CONTENT_MARKERS = (
    "fix the auth bug",  # the user's prompt
    "Reading the handler.",  # the assistant's text
    "file body",  # a tool result
    "a.py",  # a tool argument
)
"""Every distinct payload `_write_transcript` puts on disk.

Named here rather than inlined so the content gate's test breaks the day the
fixture grows a payload the assertion forgot to look for — an absence assertion
is only as strong as its list of things to be absent.
"""


def _write_transcript(claude_home: Path, cwd: Path, sid: str, *, turns: int = 1) -> None:
    """Complete user turns with a tool loop and final assistant response."""
    lines = [
        '{"type":"user","uuid":"u0","timestamp":"2026-08-09T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"fix the auth bug"}}'
    ]
    for turn in range(turns):
        if turn:
            lines.append(
                f'{{"type":"user","uuid":"u{turn}",'
                f'"timestamp":"2026-08-09T10:{turn * 4:02d}:00.000Z",'
                f'"isSidechain":false,"message":{{"role":"user","content":"next {turn}"}}}}'
            )
        lines.append(
            f'{{"type":"assistant","uuid":"a{turn}","requestId":"r{turn}","isSidechain":false,'
            f'"timestamp":"2026-08-09T10:{turn * 4 + 1:02d}:00.000Z","message":{{"id":"m{turn}",'
            '"role":"assistant","model":"claude-opus-5","stop_reason":"tool_use",'
            '"usage":{"input_tokens":10,"output_tokens":4},"content":['
            '{"type":"text","text":"Reading the handler."},'
            f'{{"type":"tool_use","id":"tu{turn}","name":"Read","input":{{"path":"a.py"}}}}]}}}}'
        )
        lines.append(
            f'{{"type":"user","uuid":"tr{turn}",'
            f'"timestamp":"2026-08-09T10:{turn * 4 + 2:02d}:00.000Z",'
            '"isSidechain":false,"message":{"role":"user","content":['
            f'{{"type":"tool_result","tool_use_id":"tu{turn}","content":"file body"}}]}}}}'
        )
        lines.append(
            f'{{"type":"assistant","uuid":"f{turn}","requestId":"rf{turn}",'
            f'"isSidechain":false,"timestamp":"2026-08-09T10:{turn * 4 + 3:02d}:00.000Z",'
            f'"message":{{"id":"fm{turn}","role":"assistant","model":"claude-final",'
            '"stop_reason":"end_turn","usage":{"input_tokens":2,"output_tokens":1},'
            '"content":[{"type":"text","text":"Done."}]}}'
        )
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


ORPHAN_THREAD_ID = "a3131313131313131"
"""A sub-agent thread whose sidecar records NO `toolUseId` — the 27% case."""


def _write_transcript_with_orphan_subagent(claude_home: Path, cwd: Path, sid: str) -> None:
    """A normal turn plus a sub-agent thread with no recoverable spawn call.

    Layout copied from a real on-host thread: `<session>/subagents/agent-<id>`,
    the sidechain lines keyed by `agentId` with `sessionId` naming the PARENT.
    The sidecar deliberately carries `spawnDepth` and no `toolUseId`, which is
    the shape 278 of 1025 real sub-agent threads have. Its messages land INSIDE
    the parent turn's bounds, because that instant is all that is left to place
    it by.
    """
    _write_transcript(claude_home, cwd, sid)
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd) / sid / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"agent-{ORPHAN_THREAD_ID}.jsonl").write_text(
        "\n".join(
            [
                '{"type":"user","uuid":"su0","timestamp":"2026-08-09T10:01:30.000Z",'
                f'"isSidechain":true,"agentId":"{ORPHAN_THREAD_ID}","sessionId":"{sid}",'
                f'"cwd":"{cwd}","message":{{"role":"user","content":"audit the migration"}}}}',
                '{"type":"assistant","uuid":"sa0","requestId":"sr0","isSidechain":true,'
                f'"agentId":"{ORPHAN_THREAD_ID}","sessionId":"{sid}","cwd":"{cwd}",'
                '"timestamp":"2026-08-09T10:02:30.000Z","message":{"id":"sm0",'
                '"role":"assistant","model":"claude-opus-5","stop_reason":"end_turn",'
                '"usage":{"input_tokens":5,"output_tokens":2},'
                '"content":[{"type":"text","text":"The migration is reversible."}]}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (folder / f"agent-{ORPHAN_THREAD_ID}.meta.json").write_text(
        json.dumps({"spawnDepth": 1, "name": "auditor", "agentType": "general-purpose"}),
        encoding="utf-8",
    )


def _names(exporter: InMemorySpanExporter) -> list[str]:
    return [span.name for span in exporter.get_finished_spans()]


def _by_name(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    matches = [span for span in exporter.get_finished_spans() if span.name == name]
    assert len(matches) == 1, f"expected exactly one {name!r}, got {len(matches)}"
    return matches[0]


# ─── the content-ownership gate (the rule that blocks this story) ────────────


def test_external_content_owner_emits_context_only_never_the_transcript(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """The DEFAULT for claude_code: the host's own Stop hook already exports
    this transcript's content, so Grove must contribute context and nothing
    else — even with the transcript sitting right there, readable."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    forwarder = _forwarder(registry, sink)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    assert _names(exporter) == [CONTEXT_SPAN_NAME]
    context = _by_name(exporter, CONTEXT_SPAN_NAME)
    assert context.attributes is not None
    # The context span DOES carry an input and an output — a trace LangFuse
    # lists with neither is one a human never opens. What the gate forbids is
    # TRANSCRIPT content, so the assertion is about provenance, not presence:
    # every word here is Grove's own record of the workspace, and not one is
    # the prompt, the completion or the tool payload sitting readable on disk.
    rendered = "{}\n{}".format(
        context.attributes["langfuse.observation.input"],
        context.attributes["langfuse.observation.output"],
    )
    assert "fix-auth" in rendered  # the workspace title: Grove's own fact
    for transcript_word in _TRANSCRIPT_CONTENT_MARKERS:
        assert transcript_word not in rendered


def test_grove_content_owner_emits_the_full_tree_beside_the_context_span(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Naming a kind ``grove`` means nothing else emits its content, so the
    replay runs and the context span rides alongside it."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    names = _names(exporter)
    assert CONTEXT_SPAN_NAME in names
    assert _agent_span_name(f"session:{SESSION_ID}") in names
    assert _generation_span_name("claude-opus-5") in names
    assert _tool_span_name("Read") in names
    generation = _by_name(exporter, _generation_span_name("claude-opus-5"))
    assert generation.attributes is not None
    assert generation.attributes["langfuse.observation.input"] == "fix the auth bug"
    # `ChatMessage.flatten` renders a tool call as its own compact JSON line
    # rather than dropping it, so a turn's flattened output is prose PLUS the
    # tool it invoked — never just the prose.
    assert generation.attributes["langfuse.observation.output"] == (
        'Reading the handler.\n{"tool_call":"Read","arguments":{"path":"a.py"}}'
    )


# ─── pricing wiring (#454 scope item 3, "wire cost_estimator to PriceBook") ──


def test_a_pricing_config_prices_the_replayed_generation(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Passing ``pricing`` builds a real ``PriceBook`` and wires it into the
    instrumentor, so a replayed generation for a KNOWN model carries a full
    ``cost_details`` breakdown end to end — not just at the unit level."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    pricing = UsagePricingConfig(models={"claude-opus-5": ModelPriceConfig(input=3.0, output=15.0)})
    forwarder = _forwarder(registry, sink, cfg=cfg, pricing=pricing)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    generation = _by_name(exporter, _generation_span_name("claude-opus-5"))
    assert generation.attributes is not None
    cost = json.loads(generation.attributes["langfuse.observation.cost_details"])
    # 10 input tokens @ $3/M + 4 output tokens @ $15/M, each class named.
    assert cost == pytest.approx({"input": 3e-05, "output": 6e-05, "total": 9e-05})
    # The two blobs describe the same classes under the same names.
    usage = json.loads(generation.attributes["langfuse.observation.usage_details"])
    assert set(cost) - {"total"} <= set(usage)


def test_cost_details_total_is_the_sum_of_the_parts_beside_it(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """A total that disagrees with its breakdown is believed by whichever a
    reader looks at first, so it is derived from the very parts emitted."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID, turns=2)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    pricing = UsagePricingConfig(
        models={"claude-opus-5": ModelPriceConfig(input=3.33, output=15.77)}
    )
    forwarder = _forwarder(registry, sink, cfg=cfg, pricing=pricing)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    for span in exporter.get_finished_spans():
        assert span.attributes is not None
        raw = span.attributes.get("langfuse.observation.cost_details")
        if raw is None:
            continue
        cost = json.loads(str(raw))
        parts = {name: value for name, value in cost.items() if name != "total"}
        assert parts
        assert cost["total"] == pytest.approx(sum(parts.values()))


def test_an_unpriced_model_yields_no_cost_details_at_all(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Prices configured for a DIFFERENT model leave this generation's cost
    unknown — no empty object, no zeros, no attribute."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    pricing = UsagePricingConfig(models={"some-other-model": ModelPriceConfig(input=3.0)})
    forwarder = _forwarder(registry, sink, cfg=cfg, pricing=pricing)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    generation = _by_name(exporter, _generation_span_name("claude-opus-5"))
    assert generation.attributes is not None
    assert "langfuse.observation.cost_details" not in generation.attributes


def test_no_pricing_config_leaves_cost_details_absent(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """``pricing=None`` (the default) must not fabricate a cost — same
    behaviour as before this seam existed."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    generation = _by_name(exporter, _generation_span_name("claude-opus-5"))
    assert generation.attributes is not None
    assert "langfuse.observation.cost_details" not in generation.attributes


def test_an_empty_price_catalog_emits_no_cost_attribute(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Grove ships no rates of its own, so a pricing section with an empty
    catalog — the default — must price nothing rather than fall back to a
    baked-in table. A span carrying cost here would mean Grove invented money."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg, pricing=UsagePricingConfig())

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    for span in exporter.get_finished_spans():
        assert span.attributes is not None
        assert "langfuse.observation.cost_details" not in span.attributes


def test_the_gate_is_per_kind_so_two_harnesses_can_differ(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """A config naming only codex as Grove-owned must not turn claude_code's
    content on — the map is keyed by kind precisely because the harnesses have
    different baseline emitters."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"codex": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    assert _names(exporter) == [CONTEXT_SPAN_NAME]


def test_a_content_owner_whose_adapter_reads_no_spine_emits_context_only(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """A bare shell records no conversation, so its adapter answers the promoted
    ``read_messages`` with an honest ``()``. Naming such a kind as a content
    owner is a config statement Grove cannot honour — it degrades to context,
    never to a raise into the poll loop."""
    exporter, sink = exported
    cfg = TelemetryConfig(enabled=True, content_owner={"generic": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    state = _state(worktree, agent_kind="generic")
    for job in forwarder.evaluate(_delta(_row(state, _session(kind="generic")))):
        forwarder.forward(job)

    assert _names(exporter) == [CONTEXT_SPAN_NAME]


# ─── the enriched context span ───────────────────────────────────────────────


def test_context_span_carries_the_live_facts_tier_one_cannot_express(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """Resource attributes freeze at process start; phase, tickets, the branch
    the agent actually created and the reconciled status all move afterwards."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    state = _state(
        worktree,
        ticket_refs=[
            TicketRef(provider="gitea", id="456", url="https://git.example.com/o/r/issues/456")
        ],
    )
    row = _row(
        state,
        _session(),
        branch="feat/otel-enricher",
        phase=PhaseReport(phase="implementing", note="wiring the forwarder", updated_at=T0),
        todo=TodoProgress(total=4, completed=1, in_progress=1, pending=2),
    )

    for job in forwarder.evaluate(_delta(row)):
        forwarder.forward(job)

    attrs = _by_name(exporter, CONTEXT_SPAN_NAME).attributes
    assert attrs is not None
    # The join key — Grove and the agent never share a trace id.
    assert attrs["langfuse.session.id"] == SESSION_ID
    assert attrs["grove.workspace.id"] == "ws1"
    assert attrs["grove.workspace.status"] == "running"
    # The LIVE branch, not the create-time snapshot on the record.
    assert attrs["grove.branch"] == "feat/otel-enricher"
    assert state.branch == "grove/fix-auth"
    assert attrs["grove.phase"] == "implementing"
    assert attrs["grove.phase.note"] == "wiring the forwarder"
    assert attrs["grove.ticket.ids"] == "456"
    assert attrs["grove.ticket.urls"] == "https://git.example.com/o/r/issues/456"
    assert attrs["grove.todo.total"] == 4
    assert attrs["grove.agent.state"] == "working"


def test_context_span_omits_what_grove_has_nothing_to_say_about(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """An absent fact is an omitted attribute, never an empty one — the same
    rule `compose_resource_attributes` follows for the frozen half."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    attrs = _by_name(exporter, CONTEXT_SPAN_NAME).attributes
    assert attrs is not None
    assert "grove.phase" not in attrs
    assert "grove.ticket.ids" not in attrs
    assert "grove.todo.total" not in attrs


def test_context_revision_is_an_immutable_trace_grouped_with_content(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Context is a point revision, not a growing observation under content."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    context = _by_name(exporter, CONTEXT_SPAN_NAME)
    root = _by_name(exporter, _agent_span_name(f"session:{SESSION_ID}"))
    assert context.context is not None and root.context is not None
    assert root.context.trace_id == derive_trace_id(SESSION_ID)
    assert context.context.trace_id != root.context.trace_id
    assert context.start_time == context.end_time
    assert context.parent is None


def test_n_context_revisions_share_one_trace_id(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """Every revision of one session's context is a SIBLING observation under
    ONE trace, not a fresh single-span trace per attribute change (#501: 8 of
    14 traces in one live session were exactly this, each rendering empty)."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    state = _state(worktree)

    for phase in ("scoping", "planning", "implementing", "verifying", "delivering"):
        row = _row(state, _session(), phase=PhaseReport(phase=phase, updated_at=T0))
        for job in forwarder.evaluate(_delta(row)):
            forwarder.forward(job)

    spans = [span for span in exporter.get_finished_spans() if span.name == CONTEXT_SPAN_NAME]
    assert len(spans) == 5
    trace_ids = {span.context.trace_id for span in spans if span.context is not None}
    assert trace_ids == {derive_trace_id(f"{SESSION_ID}/context")}
    # Distinct revisions still mint distinct, immutable span ids.
    span_ids = {span.context.span_id for span in spans if span.context is not None}
    assert len(span_ids) == 5


# ─── no repeated re-export ───────────────────────────────────────────────────


def test_an_unchanged_context_re_exports_nothing(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """A busy session emits a delta per tick even when its CONTEXT has not
    moved; a span per tick per workspace is the storm this gate exists for."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    delta = _delta(_row(_state(worktree), _session()))

    for _ in range(5):
        for job in forwarder.evaluate(delta):
            forwarder.forward(job)

    assert _names(exporter) == [CONTEXT_SPAN_NAME]


def test_a_moved_phase_re_exports_the_context_span_once(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """The gate is on the context's CONTENT, so a real change still streams."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    state = _state(worktree)
    for phase in ("planning", "planning", "implementing", "implementing"):
        row = _row(state, _session(), phase=PhaseReport(phase=phase, updated_at=T0))
        for job in forwarder.evaluate(_delta(row)):
            forwarder.forward(job)

    phases = [
        span.attributes["grove.phase"]
        for span in exporter.get_finished_spans()
        if span.attributes is not None
    ]
    assert phases == ["planning", "implementing"]


def test_a_phase_rewrite_with_an_unchanged_phase_emits_no_new_span(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """`phase.json`'s own `updated_at` moves on every rewrite even when the
    reported phase does not (an agent re-saving mid-task) — that churn key
    must not, on its own, mint a new context revision (#501)."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    state = _state(worktree)
    first = PhaseReport(phase="implementing", note="wiring", updated_at=T0)
    restated = PhaseReport(phase="implementing", note="wiring", updated_at=T0.replace(minute=5))

    for job in forwarder.evaluate(_delta(_row(state, _session(), phase=first))):
        forwarder.forward(job)
    for job in forwarder.evaluate(_delta(_row(state, _session(), phase=restated))):
        forwarder.forward(job)

    spans = [span for span in exporter.get_finished_spans() if span.name == CONTEXT_SPAN_NAME]
    assert len(spans) == 1


def test_a_quiet_session_replays_no_content_twice(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """The instrumentor's per-session watermark is a COST control and this is
    the loop that pays it: repeated ticks over an unchanged transcript must add
    no spans at all."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)
    delta = _delta(_row(_state(worktree), _session()))

    for job in forwarder.evaluate(delta):
        forwarder.forward(job)
    after_first = len(exporter.get_finished_spans())
    for _ in range(3):
        for job in forwarder.evaluate(delta):
            forwarder.forward(job)

    assert len(exporter.get_finished_spans()) == after_first


def test_an_appended_turn_replays_only_what_is_new(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID, turns=1)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)
    delta = _delta(_row(_state(worktree), _session()))

    for job in forwarder.evaluate(delta):
        forwarder.forward(job)
    exporter.clear()
    _write_transcript(claude_home, worktree, SESSION_ID, turns=2)
    for job in forwarder.evaluate(delta):
        forwarder.forward(job)

    names = _names(exporter)
    # The first immutable trace does not re-ride; only turn two appears.
    assert names.count(_agent_span_name(f"session:{SESSION_ID}:turn:2")) == 1
    assert names.count(_generation_span_name("claude-opus-5")) == 1


# ─── a fleet's sub-agent threads mint no context trace of their own (#504) ───


def test_a_fleets_sub_agent_threads_mint_no_context_trace_of_their_own(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """#504: a workspace whose agent spawned two sub-agents produced THREE
    `grove:<title>` context traces in one LangFuse session, not one, because
    `evaluate` iterated every entry in `row.sessions` unfiltered and minted a
    trace per discovered session id. Only the primary session (no
    `parent_session_id`) may mint one — see `evaluate`'s inline comment for
    why skipping the others loses no content."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    row = _row(
        _state(worktree),
        _session(),
        sessions=(
            _session(),
            _subagent_session(SUBAGENT_ID_1),
            _subagent_session(SUBAGENT_ID_2),
        ),
    )

    for job in forwarder.evaluate(_delta(row)):
        forwarder.forward(job)

    assert _names(exporter) == [CONTEXT_SPAN_NAME]
    context = _by_name(exporter, CONTEXT_SPAN_NAME)
    assert context.attributes is not None
    # The join key is the PRIMARY session's, never a sub-agent thread's.
    assert context.attributes["langfuse.session.id"] == SESSION_ID


def test_the_primary_sessions_context_span_is_unaffected_by_sibling_sub_agents(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """The one span that DOES get emitted still carries every fact the
    single-session case carries (see `test_context_span_carries_the_live_facts
    _tier_one_cannot_express`) — sub-agent threads riding beside the primary in
    `row.sessions` must not change what the primary's own span says."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    state = _state(
        worktree,
        ticket_refs=[
            TicketRef(provider="gitea", id="456", url="https://git.example.com/o/r/issues/456")
        ],
    )
    row = _row(
        state,
        _session(),
        sessions=(_session(), _subagent_session(SUBAGENT_ID_1)),
        branch="feat/otel-enricher",
        phase=PhaseReport(phase="implementing", note="wiring the forwarder", updated_at=T0),
        todo=TodoProgress(total=4, completed=1, in_progress=1, pending=2),
    )

    for job in forwarder.evaluate(_delta(row)):
        forwarder.forward(job)

    attrs = _by_name(exporter, CONTEXT_SPAN_NAME).attributes
    assert attrs is not None
    assert attrs["langfuse.session.id"] == SESSION_ID
    assert attrs["grove.branch"] == "feat/otel-enricher"
    assert attrs["grove.phase"] == "implementing"
    assert attrs["grove.ticket.ids"] == "456"
    assert attrs["grove.todo.total"] == 4


def test_a_sub_agent_thread_is_never_handed_its_own_forwarding_job(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """Pins the decision #504 asks for explicitly, so the next reader cannot
    flip it by accident: a sub-agent thread is filtered out before
    `_evaluate_session` ever runs, so it can neither mint a context revision
    NOR claim a content replay — this is "skip the thread entirely", never
    "claim its content but skip its context".

    Content is not lost by this choice: `TraceInstrumentor.plan` already
    discovers and nests every sub-agent thread INSIDE the primary session's
    own replay (`_Fleet.of`, walking spawn edges recursively — see
    `test_a_subagent_with_no_recorded_spawn_call_is_still_replayed`). A
    second, independent claim keyed on the thread's own id would only ever
    have found that thread's OWN transcript file, every message on it
    `is_sidechain=True`, which `plan()`'s `main_thread` filter always empties
    to zero manifests — so this filter removes a job that could never have
    produced a span, not one that used to.
    """
    _, sink = exported
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)
    row = _row(
        _state(worktree),
        _session(),
        sessions=(
            _session(),
            _subagent_session(SUBAGENT_ID_1),
            _subagent_session(SUBAGENT_ID_2),
        ),
    )

    jobs = forwarder.evaluate(_delta(row))

    assert [job.session_id for job in jobs] == [SESSION_ID]


def test_a_single_session_row_is_unaffected_by_the_sub_agent_filter(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """Today's shape — one primary session, no fleet — must still mint its
    context span exactly as before: the #504 fix removes only the EXTRA jobs
    a fleet contributes, never the one job every pre-existing test relies on."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    assert _names(exporter) == [CONTEXT_SPAN_NAME]


# ─── disabled is indistinguishable from absent; failures never escape ────────


def test_from_config_is_none_when_telemetry_is_disabled(registry: RepoRegistry) -> None:
    assert TraceForwarder.from_config(TelemetryConfig(), registry=registry) is None


def test_from_config_is_none_when_credentials_do_not_resolve(
    registry: RepoRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enabled but unresolvable is still ``None`` — `build_span_sink` has
    already warned, and a forwarder holding a dead sink would re-warn per tick."""
    for name in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert TraceForwarder.from_config(TelemetryConfig(enabled=True), registry=registry) is None


def test_a_workspace_changed_delta_carries_no_row_and_is_ignored(
    registry: RepoRegistry, exported: tuple[InMemorySpanExporter, SpanSink], worktree: Path
) -> None:
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    lifecycle = DashboardDelta(
        kind="workspace_changed", seq=1, workspace_id="ws1", repo_root=str(worktree.parent.parent)
    )
    assert forwarder.evaluate(lifecycle) == []
    assert exporter.get_finished_spans() == ()


def test_an_exporting_failure_never_raises_into_the_poll_path(
    registry: RepoRegistry, worktree: Path
) -> None:
    """A sink that throws is a debug line: telemetry must never cost the fleet
    its dashboard."""

    class _BrokenSink:
        def __call__(self, record: object) -> None:
            raise RuntimeError("boom")

        def flush(self) -> None:
            raise RuntimeError("boom")

    forwarder = _forwarder(registry, _BrokenSink())  # type: ignore[arg-type]
    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)  # never raises


def test_a_failing_replay_releases_its_in_flight_claim(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """The in-flight guard is a queue depth control, not a latch — a job that
    dies must not wedge its session out of every future replay."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)
    delta = _delta(_row(_state(worktree), _session()))

    def _boom(*_: object, **__: object) -> None:
        raise RuntimeError("registry down")

    # A nested context rather than the test's own `monkeypatch`: undoing that
    # one would also undo the `claude_home` fixture's redirect, and the replay
    # would then find nothing for a reason that has nothing to do with the claim.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(RepoRegistry, "get", _boom)
        for job in forwarder.evaluate(delta):
            forwarder.forward(job)
    for job in forwarder.evaluate(delta):
        forwarder.forward(job)

    assert _agent_span_name(f"session:{SESSION_ID}") in _names(exporter)


# ─── bind / close ────────────────────────────────────────────────────────────


def test_bind_subscribes_and_close_drains_and_unsubscribes(
    registry: RepoRegistry, exported: tuple[InMemorySpanExporter, SpanSink], worktree: Path
) -> None:
    """The `NotificationBroker.bind` contract: takes the bus's `subscribe`
    callable, returns an unsubscribe, and `close` drains the worker before it
    releases anything."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    callbacks: list[Callable[[DashboardDelta], None]] = []
    unsubscribed: list[bool] = []

    def _subscribe(callback: Callable[[DashboardDelta], None]) -> Callable[[], None]:
        callbacks.append(callback)
        return lambda: unsubscribed.append(True)

    forwarder.bind(_subscribe)
    assert len(callbacks) == 1
    callbacks[0](_delta(_row(_state(worktree), _session())))
    forwarder.close()

    assert unsubscribed == [True]
    assert _names(exporter) == [CONTEXT_SPAN_NAME]


def test_a_delta_arriving_before_bind_is_dropped_rather_than_exported(
    registry: RepoRegistry, exported: tuple[InMemorySpanExporter, SpanSink], worktree: Path
) -> None:
    """An unbound forwarder has no worker to run the export on, and running it
    inline would put a transcript parse and an OTLP flush on the caller."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    forwarder._on_delta(_delta(_row(_state(worktree), _session())))
    assert exporter.get_finished_spans() == ()


def test_the_context_span_attributes_survive_a_json_round_trip(
    registry: RepoRegistry, exported: tuple[InMemorySpanExporter, SpanSink], worktree: Path
) -> None:
    """Every attribute must be a scalar OTel accepts — a stray dataclass or
    datetime is dropped by the SDK with a warning nobody reads."""
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    row = _row(
        _state(worktree),
        _session(),
        phase=PhaseReport(phase="verifying", updated_at=T0),
        todo=TodoProgress(total=2, completed=2, in_progress=0, pending=0),
    )
    for job in forwarder.evaluate(_delta(row)):
        forwarder.forward(job)

    attrs = _by_name(exporter, CONTEXT_SPAN_NAME).attributes
    assert attrs is not None
    json.dumps(dict(attrs))


def test_the_join_key_is_the_launch_stamp_not_the_discovered_session(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
) -> None:
    """A codex workspace mints nothing, so Grove stamps its own tmux session as
    `langfuse.session.id` at launch and the agent's spans carry that. The spine
    must publish under the SAME value: keying by the id discovered from the
    rollout would put Grove's half in a session the agent's half can never join,
    which is two half-empty sessions rather than one whole one.

    The harness's own id is not lost — it is recorded beside the join key.
    """
    exporter, sink = exported
    forwarder = _forwarder(registry, sink)
    native = "019fe92f-a6d1-7253-a4e8-9a1506d09cf0"
    state = _state(worktree, agent_session_id=None, agent_kind="codex", agent_name="codex")

    for job in forwarder.evaluate(_delta(_row(state, _session(kind="codex", sid=native)))):
        forwarder.forward(job)

    attrs = _by_name(exporter, CONTEXT_SPAN_NAME).attributes
    assert attrs is not None
    assert attrs["langfuse.session.id"] == "grove-fix-auth"
    assert attrs["grove.agent.session.id"] == native


def test_every_replay_span_carries_the_workspace_identity_and_its_tags(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """The replay tier used to carry the session join key and nothing else, so
    the richest tree Grove produces was the one nobody could filter by repo,
    branch or agent. LangFuse aggregates across individual observations rather
    than only at the trace root, so the identity has to be on EVERY span — a
    fact present on the root alone is one a reader cannot narrow by."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    spans = [span for span in exporter.get_finished_spans() if span.name != CONTEXT_SPAN_NAME]
    assert spans, "sanity check: the replay should have emitted something"
    for span in spans:
        assert span.attributes is not None
        assert span.attributes["grove.repo"] == "proj"
        assert span.attributes["grove.branch"] == "grove/fix-auth"
        assert span.attributes["grove.agent.kind"] == "claude_code"
        tags = span.attributes["langfuse.trace.tags"]
        assert "grove" in tags
        assert "agent:claude_code" in tags
        assert "repo:proj" in tags


def test_a_tool_span_says_who_ran_it_without_a_tree_walk(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Provenance on the observation itself, because the walk up the tree is
    exactly what a reader cannot do in a list view: a fleet's tool calls all
    render as siblings there, and without an owner on each one the only way to
    tell a sub-agent's `Read` from the root's is to open both."""
    exporter, sink = exported
    _write_transcript(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    tool = _by_name(exporter, _tool_span_name("Read"))
    assert tool.attributes is not None
    assert tool.attributes["grove.agent.parent.id"] == SESSION_ID
    assert tool.attributes["grove.agent.depth"] == 0
    assert tool.attributes["gen_ai.agent.name"] == f"session:{SESSION_ID}"


def test_a_subagent_with_no_recorded_spawn_call_is_still_replayed(
    registry: RepoRegistry,
    exported: tuple[InMemorySpanExporter, SpanSink],
    worktree: Path,
    claude_home: Path,
) -> None:
    """Measured on 2592 real sidecars: 278 genuine sub-agents carry no
    `toolUseId`, and only 8 of those carry `parentAgentId` either — the edge
    was never written down, so there is no second key to read.

    Dropping them is not a missing EDGE, it is missing CONTENT: a thread
    nothing attaches never becomes a span, and its messages sit in the
    transcript as sidechains nobody replays. On one real session that silence
    cost 59% of the work. Such a thread is placed in the turn its own start
    instant falls in, parented to the turn root, and SAYS so — a reader
    comparing two sub-agents must be able to tell a recorded spawn edge from a
    placement derived from a clock.
    """
    exporter, sink = exported
    _write_transcript_with_orphan_subagent(claude_home, worktree, SESSION_ID)
    cfg = TelemetryConfig(enabled=True, content_owner={"claude_code": "grove"})
    forwarder = _forwarder(registry, sink, cfg=cfg)

    for job in forwarder.evaluate(_delta(_row(_state(worktree), _session()))):
        forwarder.forward(job)

    spans = exporter.get_finished_spans()
    orphan = next(
        span
        for span in spans
        if span.attributes and span.attributes.get("gen_ai.agent.id") == ORPHAN_THREAD_ID
    )
    assert orphan.attributes is not None
    assert orphan.attributes["grove.agent.attachment"] == "turn_window"
    # Its work reached the trace, which is the whole point.
    assert str(orphan.attributes.get("langfuse.observation.input"))

    # And it is not an orphan on the wire: every parent named must be emitted.
    by_id = {span.context.span_id for span in spans if span.context is not None}
    for span in spans:
        parent = span.parent
        if parent is not None:
            assert parent.span_id in by_id, f"{span.name} names a parent nothing emitted"
