"""A duration is three numbers: the union is a clock, the sum is a labour total.

Sub-agents run concurrently, so adding every thread's intervals up answers a
different question from merging them — and the projector published the sum under
the union's name until a real fleet session reported more "active" time than the
session's own lifespan. Every test here exists to keep
``active_ms <= elapsed_span_ms`` and ``active_ms <= execution_ms`` true.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core.agents import AgentActivity, AgentActivityState, AgentMessage, ContentBlock
from grove.core.agents.model import SessionRef
from grove.core.config import GroveConfig
from grove.core.contracts.usage import UsageFilters
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.usage._intervals import ActiveIntervals, WorkIntervals
from grove.core.usage._pricing import PriceBook
from grove.core.usage._store import UsageStore
from grove.core.usage.projector import UsageProjector
from grove.core.usage.query import UsageQuery

_START = datetime(2026, 8, 11, 9, tzinfo=UTC)


def test_an_unmeasurable_duration_is_absent_rather_than_zero() -> None:
    empty = ActiveIntervals()

    assert empty.union_ms() is None
    assert empty.sum_ms() is None
    measured = ActiveIntervals.of([(1000, 1000)])
    assert measured.union_ms() == 0
    assert measured.sum_ms() == 0


def test_the_union_counts_concurrent_agents_once_and_the_sum_counts_each() -> None:
    # A root agent working for 100s while two sub-agents work inside that span.
    intervals = ActiveIntervals.of([(0, 100_000), (20_000, 80_000), (30_000, 120_000)])

    assert intervals.union_ms() == 120_000
    assert intervals.sum_ms() == 100_000 + 60_000 + 90_000
    assert intervals.union_ms() < intervals.sum_ms()


def test_the_two_halves_partition_the_sum_but_never_the_union() -> None:
    """``generation_ms + tool_ms == execution_ms``, and there is no union twin.

    THE invariant of the split. It holds because addition is what the sum
    reducer does — so the arithmetic is a property of the shape rather than
    something the projector has to remember to keep true. The second assertion
    is the one that says why no ``active_ms`` counterpart exists: the two
    halves here OVERLAP (a tool ran while a sub-agent generated), so adding
    their unions counts that overlap twice, which is the exact double count
    ``union_ms`` was written to prevent.
    """
    work = WorkIntervals(
        generation=ActiveIntervals.of([(0, 40_000)]),
        tool=ActiveIntervals.of([(30_000, 100_000)]),
    )

    generation_ms, tool_ms = work.generation.sum_ms(), work.tool.sum_ms()
    assert generation_ms == 40_000
    assert tool_ms == 70_000
    assert generation_ms + tool_ms == work.combined.sum_ms() == 110_000

    # The union refuses to be partitioned, and by exactly the overlap.
    assert work.combined.union_ms() == 100_000
    assert work.generation.union_ms() + work.tool.union_ms() == 110_000


def test_an_unmeasured_half_is_absent_rather_than_zero() -> None:
    """A session that ran no tools reports ``None``, not ``0``.

    The whole-view rule applied one level down: ``sum_ms`` over no intervals is
    absent, so ``execution_ms`` equals the one measured half and the other says
    *nothing was timed here*. A fabricated ``0`` would read as "tools ran and
    took no time", which is a measurement nobody made.
    """
    generation_only = WorkIntervals(generation=ActiveIntervals.of([(0, 5_000)]))

    assert generation_only.tool.sum_ms() is None
    assert generation_only.generation.sum_ms() == generation_only.combined.sum_ms() == 5_000
    assert WorkIntervals().combined.sum_ms() is None


def test_disjoint_intervals_and_an_inverted_pair_are_both_handled_honestly() -> None:
    disjoint = ActiveIntervals.of([(50_000, 60_000), (0, 10_000)])
    assert disjoint.union_ms() == disjoint.sum_ms() == 20_000

    # Two clocks wrote the endpoints, so an end before its start is an artefact.
    assert ActiveIntervals.of([(10_000, 9_000)]).union_ms() == 0
    # Touching intervals merge into one span rather than being counted twice.
    assert ActiveIntervals.of([(0, 10_000), (10_000, 20_000)]).union_ms() == 20_000


# ── the projector: one message spine, two reducers ────────────────────────────


@dataclass
class _Adapter:
    kind: str
    ref: SessionRef
    messages: tuple[AgentMessage, ...]

    def discover_all(self) -> tuple[SessionRef, ...]:
        return (self.ref,)

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        assert self.ref.transcript_path is not None
        return [self.ref.transcript_path]

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return self.messages

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        return AgentActivity(state=AgentActivityState.WAITING)


def _at(seconds: int) -> datetime:
    return _START + timedelta(seconds=seconds)


def _subagent_thread(thread: str) -> tuple[AgentMessage, ...]:
    """One sub-agent working 10s..70s, entirely inside the root's own tool call."""
    return (
        AgentMessage(role="user", timestamp=_at(10), is_sidechain=True, thread_id=thread),
        AgentMessage(role="assistant", timestamp=_at(40), is_sidechain=True, thread_id=thread),
        AgentMessage(
            role="assistant",
            content=(
                ContentBlock(type="tool_use", tool_name="Read", tool_use_id=f"{thread}-call"),
            ),
            timestamp=_at(40),
            is_sidechain=True,
            thread_id=thread,
        ),
        AgentMessage(
            role="tool",
            content=(ContentBlock(type="tool_result", tool_use_id=f"{thread}-call"),),
            timestamp=_at(70),
            is_sidechain=True,
            thread_id=thread,
        ),
    )


def _fleet_messages() -> tuple[AgentMessage, ...]:
    root = (
        AgentMessage(role="user", timestamp=_at(0)),
        AgentMessage(role="assistant", timestamp=_at(10)),
        AgentMessage(
            role="assistant",
            content=(ContentBlock(type="tool_use", tool_name="Task", tool_use_id="fan-out"),),
            timestamp=_at(10),
        ),
        AgentMessage(
            role="tool",
            content=(ContentBlock(type="tool_result", tool_use_id="fan-out"),),
            timestamp=_at(70),
        ),
    )
    return (*root, *_subagent_thread("agent-a"), *_subagent_thread("agent-b"))


def _project(tmp_path: Path) -> UsageStore:
    transcript = tmp_path / "claude_code-profile" / "projects" / "project" / "fleet.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("{}\n", encoding="utf-8")
    ref = SessionRef(
        session_id="fleet",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=transcript,
        birth=_START,
        mtime=transcript.stat().st_mtime,
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    UsageProjector(
        cfg=cfg,
        registry=RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json")),
        store=store,
        adapters=(_Adapter(kind="claude_code", ref=ref, messages=_fleet_messages()),),
    ).refresh()
    return store


def test_concurrent_sub_agents_never_make_active_time_exceed_the_session_lifespan(
    tmp_path: Path,
) -> None:
    store = _project(tmp_path)

    row = store.query("SELECT active_ms, execution_ms, elapsed_span_ms FROM sessions")[0]
    # Root: 10s generating, then 60s inside one Task call. Two sub-agents each
    # spend 30s generating and 30s in a tool, wholly inside that call.
    assert row["execution_ms"] == 10_000 + 60_000 + 4 * 30_000
    assert row["active_ms"] == 70_000
    assert row["elapsed_span_ms"] == 70_000
    assert row["active_ms"] <= row["elapsed_span_ms"] <= row["execution_ms"]
    store.close()


def test_a_projected_session_splits_execution_time_into_model_wait_and_tool_time(
    tmp_path: Path,
) -> None:
    """The partition survives the round trip through SQLite.

    Also disproves the premise Grove#510 was filed on — *"not derivable from
    Claude Code transcripts, they record no per-request model latency"*. This
    spine is a ``claude_code`` session carrying nothing but timestamps, and the
    model wait falls straight out of them: the gap between the record that
    prompted a reply and the reply itself. No provider reports it and none has
    to. (Confirmed on the reference host's real store the same day: 149,690
    measured ``claude_code`` generation calls averaging 10.4s.)
    """
    store = _project(tmp_path)

    row = store.query("SELECT execution_ms, generation_ms, tool_ms FROM sessions")[0]
    # Root generates for 10s; each sub-agent generates for 30s.
    assert row["generation_ms"] == 10_000 + 2 * 30_000
    # The root's 60s Task call, plus each sub-agent's own 30s tool call.
    assert row["tool_ms"] == 60_000 + 2 * 30_000
    assert row["generation_ms"] + row["tool_ms"] == row["execution_ms"] == 190_000
    store.close()


def test_the_wire_carries_both_reducers_for_a_projected_session(tmp_path: Path) -> None:
    store = _project(tmp_path)
    cfg = GroveConfig()
    query = UsageQuery(store=store, prices=PriceBook(cfg.usage.pricing), cfg=cfg)

    summary = query.summary(UsageFilters())
    assert summary.duration.active_ms == 70_000
    assert summary.duration.execution_ms == 190_000
    assert summary.duration.generation_ms == 70_000
    assert summary.duration.tool_ms == 120_000
    row = query.sessions(UsageFilters(), cursor=None, limit=10, sort="recent").rows[0]
    assert row.duration.active_ms == 70_000
    assert row.duration.execution_ms == 190_000
    assert row.duration.active_ms <= (row.duration.elapsed_span_ms or 0)
    # The partition holds on every aggregate that publishes it, not just on the
    # row the projector wrote.
    for view in (summary.duration, row.duration):
        assert (view.generation_ms or 0) + (view.tool_ms or 0) == view.execution_ms
    store.close()


# ── the range-bounded read, which reduces the event spine instead ─────────────


def _insert_overlapping_events(store: UsageStore, *, at: int) -> None:
    """One session whose root and sub-agent generations both end at ``at``."""
    with store.write() as conn:
        conn.execute(
            "INSERT INTO sources(source_id, provider, root, label) "
            "VALUES('source', 'claude_code', '/profile', 'profile')"
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, project, started_at, "
            "last_event_at, active_ms, execution_ms, elapsed_span_ms, models) "
            "VALUES('overlap', 'source', 'claude_code', '/repo', ?, ?, 60000, 120000, 60000, ?)",
            (at - 60, at, json.dumps(["model-a"])),
        )
        for seq, kind in ((1, "generation"), (2, "subagent")):
            conn.execute(
                "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, duration_ms, "
                "duration_source) VALUES('overlap', 'source', ?, ?, ?, 60000, 'derived')",
                (seq, at, kind),
            )


def test_a_range_bounded_summary_merges_overlaps_instead_of_summing_them(
    tmp_path: Path,
) -> None:
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    at = int(datetime(2026, 8, 11, 12, tzinfo=UTC).timestamp())
    _insert_overlapping_events(store, at=at)
    query = UsageQuery(store=store, prices=PriceBook(cfg.usage.pricing), cfg=cfg)

    bounded = query.summary(UsageFilters(since=datetime(2026, 8, 11, tzinfo=UTC)))
    assert bounded.duration.execution_ms == 120_000
    assert bounded.duration.active_ms == 60_000

    row = query.sessions(
        UsageFilters(since=datetime(2026, 8, 11, tzinfo=UTC)),
        cursor=None,
        limit=10,
        sort="recent",
    ).rows[0]
    assert (row.duration.active_ms, row.duration.execution_ms) == (60_000, 120_000)
    # A breakdown carries one duration on the wire, and it is the honest clock.
    project = query.breakdown(
        UsageFilters(since=datetime(2026, 8, 11, tzinfo=UTC)), dimension="project"
    )
    assert project.rows[0].active_ms == 60_000
    store.close()


def test_a_range_bounded_read_partitions_the_event_spine_by_kind(tmp_path: Path) -> None:
    """The bounded path reduces EVENTS, so its split has to come from them too.

    ``execution_ms`` is a bare ``SUM(duration_ms)`` over every event in the
    window, and only ``generation``/``subagent``/``tool_result`` rows ever
    carry one — a ``tool_call`` row's duration lives on the result it is
    correlated with. That is what makes the two `CASE` sums a partition of the
    unqualified one rather than an approximation of it, and this pins it with
    a row of each kind in the window (measured on the reference store the same
    day: 0 of 180,483 ``tool_call`` rows and 0 of 34,384 ``file_edit`` rows
    carry a duration).
    """
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    at = int(datetime(2026, 8, 11, 12, tzinfo=UTC).timestamp())
    with store.write() as conn:
        conn.execute(
            "INSERT INTO sources(source_id, provider, root, label) "
            "VALUES('source', 'claude_code', '/profile', 'profile')"
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, project, started_at, "
            "last_event_at, models) VALUES('split', 'source', 'claude_code', '/repo', ?, ?, ?)",
            (at - 300, at, json.dumps(["model-a"])),
        )
        for seq, kind, duration_ms in (
            (1, "generation", 20_000),
            (2, "subagent", 30_000),
            (3, "tool_call", None),
            (4, "file_edit", None),
            (5, "tool_result", 50_000),
        ):
            conn.execute(
                "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, duration_ms) "
                "VALUES('split', 'source', ?, ?, ?, ?)",
                (seq, at, kind, duration_ms),
            )
    query = UsageQuery(store=store, prices=PriceBook(cfg.usage.pricing), cfg=cfg)

    filters = UsageFilters(since=datetime(2026, 8, 11, tzinfo=UTC))
    for view in (
        query.summary(filters).duration,
        query.sessions(filters, cursor=None, limit=10, sort="recent").rows[0].duration,
    ):
        assert view.generation_ms == 50_000
        assert view.tool_ms == 50_000
        assert view.generation_ms + view.tool_ms == view.execution_ms == 100_000
    store.close()
