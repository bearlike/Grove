"""Projection tests pin the adapter seam, cache invalidation and privacy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core.agents import AgentActivity, AgentActivityState, AgentMessage, ContentBlock
from grove.core.agents.model import SessionRef, TokenUsage
from grove.core.config import GroveConfig
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.usage._store import UsageStore
from grove.core.usage.projector import UsageProjector


@dataclass
class _Adapter:
    kind: str
    ref: SessionRef
    paths: list[Path]
    messages: tuple[AgentMessage, ...]
    activity: AgentActivity
    reads: int = 0
    on_read: Callable[[], None] | None = None

    def discover_all(self) -> tuple[SessionRef, ...]:
        return (self.ref,) if self.ref.transcript_path and self.ref.transcript_path.exists() else ()

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        assert str(cwd) == self.ref.cwd
        assert session_id == self.ref.session_id
        return self.paths

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        self.reads += 1
        if self.on_read is not None:
            callback, self.on_read = self.on_read, None
            callback()
        return self.messages

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        return self.activity


def _registry(tmp_path: Path, cfg: GroveConfig) -> RepoRegistry:
    return RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json"))


def _files(tmp_path: Path, kind: str, session_id: str) -> tuple[Path, Path, Path]:
    profile = tmp_path / f"{kind}-profile"
    marker = "projects" if kind == "claude_code" else "sessions"
    main = profile / marker / "project" / f"{session_id}.jsonl"
    child = main.parent / f"{session_id}-child.jsonl"
    main.parent.mkdir(parents=True)
    main.write_text('{"private":"prompt-secret"}\n', encoding="utf-8")
    child.write_text('{"private":"result-secret"}\n', encoding="utf-8")
    return profile, main, child


def test_projector_uses_normalized_messages_tracks_all_files_and_persists_no_content(
    tmp_path: Path,
) -> None:
    _, main, child = _files(tmp_path, "claude_code", "session-a")
    started = datetime(2026, 8, 9, 12, tzinfo=UTC)
    messages = (
        AgentMessage(
            role="user",
            content=(ContentBlock(type="text", text="prompt-secret"),),
            timestamp=started - timedelta(seconds=1),
        ),
        AgentMessage(
            role="assistant",
            content=(
                ContentBlock(type="text", text="assistant-secret"),
                ContentBlock(
                    type="tool_use",
                    tool_name="Edit",
                    tool_use_id="call-1",
                    tool_input={"file_path": "src/example.py", "new_string": "body-secret"},
                ),
            ),
            model="claude-test",
            usage=TokenUsage(input=10, output=4, cache_read=3, cache_creation=2),
            timestamp=started,
        ),
        AgentMessage(
            role="tool",
            content=(
                ContentBlock(
                    type="tool_result",
                    tool_use_id="call-1",
                    text="tool-result-secret",
                    is_error=True,
                ),
            ),
            timestamp=started + timedelta(seconds=2),
        ),
    )
    ref = SessionRef(
        session_id="session-a",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=started,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main, child],
        messages=messages,
        activity=AgentActivity(
            state=AgentActivityState.WAITING,
            human_turns=1,
            assistant_replies=1,
            model="claude-test",
            last_event_at=started + timedelta(seconds=2),
        ),
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    projector = UsageProjector(
        cfg=cfg, registry=_registry(tmp_path, cfg), store=store, adapters=(adapter,)
    )

    first = projector.refresh()
    assert first.changed_sources == 1
    assert adapter.reads == 1
    assert store.scalar("SELECT COUNT(*) FROM ingested_files") == 2
    assert [row["kind"] for row in store.query("SELECT kind FROM usage_events ORDER BY seq")] == [
        "generation",
        "file_edit",
        "tool_result",
    ]
    result = store.query(
        "SELECT tool_name, target, duration_ms FROM usage_events WHERE kind='tool_result'"
    )[0]
    assert tuple(result) == ("Edit", "src/example.py", 2000)
    generation = store.query(
        "SELECT tool_name, duration_ms, duration_source FROM usage_events WHERE kind='generation'"
    )[0]
    assert tuple(generation) == (None, 1000, "derived")
    assert store.scalar("SELECT active_ms FROM sessions") == 3000

    unchanged = projector.refresh()
    assert unchanged.changed_sources == 0
    assert adapter.reads == 1

    child.write_text('{"private":"result-secret"}\n{}\n', encoding="utf-8")
    changed = projector.refresh()
    assert changed.changed_sources == 1
    assert adapter.reads == 2
    assert store.scalar("SELECT COUNT(*) FROM usage_events") == 3

    dump = "\n".join(store.connect().iterdump())
    for secret in ("prompt-secret", "result-secret", "assistant-secret", "body-secret"):
        assert secret not in dump

    main.unlink()
    vanished = projector.refresh()
    assert vanished.changed_sources == 1
    assert store.scalar("SELECT COUNT(*) FROM sessions") == 0
    assert store.scalar("SELECT COUNT(*) FROM usage_events") == 0
    store.close()


def test_codex_cumulative_tokens_stay_provider_total_not_fresh_input(tmp_path: Path) -> None:
    _, main, _ = _files(tmp_path, "codex", "session-c")
    at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    ref = SessionRef(
        session_id="session-c",
        adapter_kind="codex",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=at,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="codex",
        ref=ref,
        paths=[main],
        messages=(AgentMessage(role="assistant", model="gpt-test", timestamp=at),),
        activity=AgentActivity(
            state=AgentActivityState.WAITING,
            model="gpt-test",
            tokens_in=100,
            tokens_out=50,
            last_event_at=at,
        ),
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    UsageProjector(
        cfg=cfg, registry=_registry(tmp_path, cfg), store=store, adapters=(adapter,)
    ).refresh()

    row = store.query("SELECT fresh_input, output, provider_total FROM sessions")[0]
    assert tuple(row) == (None, 50, 150)
    event = store.query(
        "SELECT tool_name, provider_total FROM usage_events WHERE kind='generation'"
    )[0]
    assert tuple(event) == (None, 150)
    store.close()


def test_subagent_messages_partition_into_separate_nullable_columns(tmp_path: Path) -> None:
    """``is_sidechain`` messages already ride the same spine the root thread's
    do (the adapter's ``locate_transcripts`` recursively globs sub-agent
    files), so the projector sums them into the combined totals it always
    computed — attribution: a session's total includes the work it
    delegated. It ALSO separates them into ``subagent_*`` columns so a reader
    can see how much of that total was delegated. Re-running must REPLACE,
    never double-count."""
    _, main, child = _files(tmp_path, "claude_code", "session-fleet")
    started = datetime(2026, 8, 9, 12, tzinfo=UTC)
    messages = (
        AgentMessage(
            role="assistant",
            content=(),
            model="claude-test",
            usage=TokenUsage(input=100, output=40, cache_read=10, cache_creation=5),
            timestamp=started,
        ),
        AgentMessage(
            role="assistant",
            content=(),
            model="claude-test",
            usage=TokenUsage(input=7, output=3, cache_read=2000, cache_creation=1),
            timestamp=started + timedelta(seconds=1),
            is_sidechain=True,
            thread_id="agent-1",
        ),
    )
    ref = SessionRef(
        session_id="session-fleet",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=started,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main, child],
        messages=messages,
        activity=AgentActivity(
            state=AgentActivityState.WAITING, last_event_at=started + timedelta(seconds=1)
        ),
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    projector = UsageProjector(
        cfg=cfg, registry=_registry(tmp_path, cfg), store=store, adapters=(adapter,)
    )

    projector.refresh()
    row = store.query(
        "SELECT fresh_input, cache_read, output, "
        "subagent_fresh_input, subagent_cache_read, subagent_output, subagent_reasoning "
        "FROM sessions"
    )[0]
    assert (row["fresh_input"], row["cache_read"], row["output"]) == (107, 2010, 43)
    assert (row["subagent_fresh_input"], row["subagent_cache_read"], row["subagent_output"]) == (
        7,
        2000,
        3,
    )
    assert row["subagent_reasoning"] is None  # unreported, never a fabricated 0

    projector.refresh(force=True)  # replace, not accumulate
    replayed = store.query("SELECT fresh_input, subagent_cache_read FROM sessions")[0]
    assert (replayed["fresh_input"], replayed["subagent_cache_read"]) == (107, 2000)
    store.close()


def test_projector_marks_discovered_transcript_without_cwd_degraded(tmp_path: Path) -> None:
    _, main, _ = _files(tmp_path, "claude_code", "session-no-cwd")
    at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    ref = SessionRef(
        session_id="session-no-cwd",
        adapter_kind="claude_code",
        cwd=None,
        transcript_path=main,
        birth=at,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main],
        messages=(),
        activity=AgentActivity(state=AgentActivityState.UNKNOWN),
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")

    result = UsageProjector(
        cfg=cfg, registry=_registry(tmp_path, cfg), store=store, adapters=(adapter,)
    ).refresh()

    assert result.degraded_sources == 1
    source = store.query("SELECT health, detail FROM sources")[0]
    assert source["health"] == "degraded"
    assert source["detail"] == "transcript cwd was not measured"
    assert adapter.reads == 0
    store.close()


def test_append_during_adapter_read_is_seen_on_next_refresh(tmp_path: Path) -> None:
    _, main, _ = _files(tmp_path, "claude_code", "session-growing")
    at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    ref = SessionRef(
        session_id="session-growing",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=at,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main],
        messages=(AgentMessage(role="assistant", timestamp=at),),
        activity=AgentActivity(state=AgentActivityState.WAITING, last_event_at=at),
        on_read=lambda: main.write_text("first\nappended\n", encoding="utf-8"),
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    projector = UsageProjector(
        cfg=cfg, registry=_registry(tmp_path, cfg), store=store, adapters=(adapter,)
    )

    projector.refresh()
    second = projector.refresh()

    assert second.changed_sources == 1
    assert adapter.reads == 2
    store.close()


def test_apply_patch_metadata_counts_all_changed_files(tmp_path: Path) -> None:
    _, main, _ = _files(tmp_path, "codex", "session-patch")
    at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    patch = """*** Begin Patch
*** Update File: src/a.py
*** Delete File: src/b.py
*** End Patch"""
    ref = SessionRef(
        session_id="session-patch",
        adapter_kind="codex",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=at,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="codex",
        ref=ref,
        paths=[main],
        messages=(
            AgentMessage(
                role="assistant",
                timestamp=at,
                content=(
                    ContentBlock(
                        type="tool_use",
                        tool_name="apply_patch",
                        tool_use_id="patch-1",
                        tool_input={"input": patch},
                    ),
                ),
            ),
        ),
        activity=AgentActivity(state=AgentActivityState.WAITING, last_event_at=at),
    )
    cfg = GroveConfig()
    store = UsageStore(tmp_path / "usage.db")
    UsageProjector(
        cfg=cfg, registry=_registry(tmp_path, cfg), store=store, adapters=(adapter,)
    ).refresh()

    assert store.scalar("SELECT files_changed FROM sessions") == 2
    assert store.scalar("SELECT target FROM usage_events WHERE kind='file_edit'") == "src/a.py"
    store.close()


def test_relaxing_retention_forces_unchanged_transcript_rebuild(tmp_path: Path) -> None:
    _, main, _ = _files(tmp_path, "claude_code", "session-retained")
    at = datetime(2026, 8, 8, 12, tzinfo=UTC)
    ref = SessionRef(
        session_id="session-retained",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=at,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main],
        messages=(AgentMessage(role="assistant", timestamp=at),),
        activity=AgentActivity(state=AgentActivityState.WAITING, last_event_at=at),
    )
    strict = GroveConfig.model_validate({"usage": {"retention_days": 0}})
    store = UsageStore(tmp_path / "usage.db")
    UsageProjector(
        cfg=strict,
        registry=_registry(tmp_path, strict),
        store=store,
        adapters=(adapter,),
        clock=lambda: at + timedelta(days=1),
    ).refresh()
    assert store.scalar("SELECT COUNT(*) FROM sessions") == 0

    relaxed = GroveConfig.model_validate({"usage": {"retention_days": None}})
    result = UsageProjector(
        cfg=relaxed,
        registry=_registry(tmp_path, relaxed),
        store=store,
        adapters=(adapter,),
        clock=lambda: at + timedelta(days=1),
    ).refresh()

    assert result.changed_sources == 1
    assert store.scalar("SELECT COUNT(*) FROM sessions") == 1
    store.close()


@dataclass
class _FakeWorkspaceState:
    """Just enough of ``WorkspaceState`` for the ``_workspace_index`` seam."""

    id: str
    agent_session_id: str | None


@dataclass
class _FakeManagerConfig:
    agents: tuple[object, ...] = ()


class _FakeManager:
    """Stands in for a real ``WorkspaceManager`` — no git/tmux reconciliation.

    ``list_calls`` is the whole point: the fix under test replaced a
    per-session ``manager.list()`` walk (the live git/tmux reconciliation
    profiled at 394 calls / 3.098s for 79 sessions) with one walk per
    *refresh*, so this must stay constant as the number of sessions changing
    in one refresh grows.
    """

    def __init__(self, states: list[_FakeWorkspaceState], kind: str) -> None:
        self._states = states
        self._kind = kind
        self.config = _FakeManagerConfig()
        self.list_calls = 0

    def list(self) -> list[_FakeWorkspaceState]:
        self.list_calls += 1
        return self._states

    def effective_kind(self, state: _FakeWorkspaceState) -> str:
        return self._kind


@dataclass
class _MultiAdapter:
    """One adapter, several changed sessions — the shape the index fix is for.

    Three SEPARATE `_Adapter`s would not exercise it: a source is keyed by
    `(adapter_kind, profile_root)`, so three fakes reporting one profile root
    collapse into ONE source and the projector legitimately walks a single ref.
    The real cost this fix removes is many sessions under one adapter, which is
    exactly what a host with a busy fleet has.
    """

    kind: str
    refs: tuple[SessionRef, ...]
    paths_by_session: dict[str, list[Path]]
    messages: tuple[AgentMessage, ...]
    activity: AgentActivity

    def discover_all(self) -> tuple[SessionRef, ...]:
        return self.refs

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        return self.paths_by_session[session_id]

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return self.messages

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        return self.activity


class _FakeRegistry:
    def __init__(self, manager: _FakeManager, root: Path) -> None:
        self._manager = manager
        self._root = root

    def known_roots(self) -> tuple[Path, ...]:
        return (self._root,)

    def get(self, root: Path) -> _FakeManager:
        return self._manager


def _changing_adapter(tmp_path: Path, session_id: str, at: datetime) -> _Adapter:
    _, main, _ = _files(tmp_path, "claude_code", session_id)
    ref = SessionRef(
        session_id=session_id,
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=at,
        mtime=main.stat().st_mtime,
    )
    return _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main],
        messages=(AgentMessage(role="assistant", timestamp=at),),
        activity=AgentActivity(state=AgentActivityState.WAITING, last_event_at=at),
    )


def test_workspace_index_is_built_once_per_refresh_not_once_per_session(
    tmp_path: Path,
) -> None:
    at = datetime(2026, 8, 9, 12, tzinfo=UTC)
    cfg = GroveConfig()

    one_root = tmp_path / "one"
    one_root.mkdir()
    one_adapter = _changing_adapter(one_root, "session-a", at)
    one_manager = _FakeManager(
        [_FakeWorkspaceState(id="ws-a", agent_session_id="session-a")], kind="claude_code"
    )
    one_store = UsageStore(one_root / "usage.db")
    UsageProjector(
        cfg=cfg,
        registry=_FakeRegistry(one_manager, one_root),
        store=one_store,
        adapters=(one_adapter,),
    ).refresh()
    assert (
        one_store.scalar("SELECT workspace_id FROM sessions WHERE session_id='session-a'") == "ws-a"
    )
    calls_for_one_changed_session = one_manager.list_calls
    one_store.close()

    # ONE adapter carrying three changed sessions, not three adapters: a source
    # is keyed by `(adapter_kind, profile_root)`, so three fakes sharing a
    # profile root collapse to one source and the projector would legitimately
    # walk a single ref — the test would pass while measuring nothing.
    many_root = tmp_path / "many"
    many_root.mkdir()
    session_ids = ["session-x", "session-y", "session-z"]
    singles = [_changing_adapter(many_root / sid, sid, at) for sid in session_ids]
    many_adapter = _MultiAdapter(
        kind="claude_code",
        refs=tuple(single.ref for single in singles),
        paths_by_session={single.ref.session_id: single.paths for single in singles},
        messages=singles[0].messages,
        activity=singles[0].activity,
    )
    many_states = [_FakeWorkspaceState(id=f"ws-{sid}", agent_session_id=sid) for sid in session_ids]
    many_manager = _FakeManager(many_states, kind="claude_code")
    many_store = UsageStore(many_root / "usage.db")
    UsageProjector(
        cfg=cfg,
        registry=_FakeRegistry(many_manager, many_root),
        store=many_store,
        adapters=(many_adapter,),
    ).refresh()
    for sid in session_ids:
        assert (
            many_store.scalar(f"SELECT workspace_id FROM sessions WHERE session_id='{sid}'")
            == f"ws-{sid}"
        )
    # Three sessions changed in one refresh, not one — the whole point of the
    # fix is that this costs exactly the same `manager.list()` calls as one.
    assert many_manager.list_calls == calls_for_one_changed_session
    many_store.close()


def test_retention_drops_cross_cutoff_session_instead_of_leaking_old_totals(
    tmp_path: Path,
) -> None:
    _, main, _ = _files(tmp_path, "claude_code", "session-crossing")
    old = datetime(2026, 8, 8, 12, tzinfo=UTC)
    recent = datetime(2026, 8, 9, 12, tzinfo=UTC)
    ref = SessionRef(
        session_id="session-crossing",
        adapter_kind="claude_code",
        cwd=str(tmp_path),
        transcript_path=main,
        birth=old,
        mtime=main.stat().st_mtime,
    )
    adapter = _Adapter(
        kind="claude_code",
        ref=ref,
        paths=[main],
        messages=(
            AgentMessage(role="assistant", timestamp=old, usage=TokenUsage(input=100, output=10)),
            AgentMessage(role="assistant", timestamp=recent, usage=TokenUsage(input=50, output=5)),
        ),
        activity=AgentActivity(state=AgentActivityState.WAITING, last_event_at=recent),
    )
    cfg = GroveConfig.model_validate({"usage": {"retention_days": 1}})
    store = UsageStore(tmp_path / "usage.db")

    UsageProjector(
        cfg=cfg,
        registry=_registry(tmp_path, cfg),
        store=store,
        adapters=(adapter,),
        clock=lambda: recent + timedelta(hours=6),
    ).refresh()

    assert store.scalar("SELECT COUNT(*) FROM sessions") == 0
    assert store.scalar("SELECT COUNT(*) FROM usage_events") == 0
    store.close()
