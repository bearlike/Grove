"""ActivityService: cross-project snapshot, the status-blend policy, delta bus.

Real tmp git repos + the FakeTmux seam + a shared store/registry. The blend
truth table is tested directly against the pure staticmethod (the single policy
site); the rest goes through the real snapshot/poll paths with in-memory fakes.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from grove.core import paths as core_paths
from grove.core import tmux
from grove.core.activity import ActivityService, DashboardDelta, SessionActivity, WorkspaceActivity
from grove.core.agents import AgentActivity, AgentActivityState, AgentSession
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.activity import DashboardEvent, DashboardSnapshotView
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from tests.conftest import FakeTmux


def _iso(dt: datetime) -> str:
    """A transcript-record timestamp string for a given birth instant.

    Several fixtures below need a session born *after* the workspace's own
    ``created_at`` (real wall-clock time at test run) to pass the created_at
    adoption gate (`WorkspaceState.adopts_session`) — a timestamp hard-coded to
    a fixed past date would predate it and get filtered as stale.
    """
    return dt.isoformat().replace("+00:00", "Z")


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "t@grove.local"],
        ["config", "user.name", "Grove Test"],
    ):
        subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init", "--no-verify"], cwd=path, check=True, capture_output=True
    )
    return path.resolve()


@pytest.fixture
def env(fake_tmux: FakeTmux, tmp_path: Path) -> tuple[ActivityService, RepoRegistry]:
    # hooks explicitly OFF: several tests below (e.g.
    # test_extras_discovered_without_hooks_enabled) exist specifically to pin
    # the hooks-DISABLED behavior — pin it here so a default flip in
    # config.py can't silently change what this file exercises.
    cfg = GroveConfig.model_validate(
        {"tmux": {"session_prefix": "test-"}, "hooks": {"enabled": False}}
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    return ActivityService(registry=registry), registry


# ─── blend truth table (the single policy site) ─────────────────────────────


@pytest.mark.parametrize(
    ("ws_status", "transcript_state", "has_transcript", "provenance", "remote", "expected"),
    [
        # Nothing materialized (no file AND nothing parseable — remote adapters
        # count via parsed state instead of files): STARTING only for a
        # grove_launched session (awaiting its first turn). An fs_discovered
        # file that vanished between discover and read → UNKNOWN, never a
        # false "starting".
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            False,
            "grove_launched",
            False,
            AgentActivityState.STARTING,
        ),
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WAITING,
            False,
            "grove_launched",
            False,
            AgentActivityState.STARTING,
        ),
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            False,
            "fs_discovered",
            False,
            AgentActivityState.UNKNOWN,
        ),
        # An ended turn stays WAITING even when tmux is fresh.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WAITING,
            True,
            "grove_launched",
            False,
            AgentActivityState.WAITING,
        ),
        # tool_use tail: ACTIVE tmux confirms WORKING; quiet tmux → IDLE. Provenance
        # is irrelevant once a file exists.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            False,
            AgentActivityState.WORKING,
        ),
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            False,
            AgentActivityState.IDLE,
        ),
        (
            WorkspaceStatus.OFFLINE,
            AgentActivityState.WORKING,
            True,
            "fs_discovered",
            False,
            AgentActivityState.IDLE,
        ),
        # Definitive transcript signals pass through.
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.UNKNOWN,
            True,
            "grove_launched",
            False,
            AgentActivityState.UNKNOWN,
        ),
        (
            WorkspaceStatus.ACTIVE,
            AgentActivityState.ERROR,
            True,
            "fs_discovered",
            False,
            AgentActivityState.ERROR,
        ),
        # BLOCKED is definitive too — erasing a needs-input prompt to IDLE on a
        # quiet pane was exactly the wrong signal to drop.
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.BLOCKED,
            True,
            "grove_launched",
            False,
            AgentActivityState.BLOCKED,
        ),
        # Remote adapter: the backend's WORKING is authoritative — the local pane
        # runs a bare shell, so tmux-quiet must not demote it to IDLE.
        (
            WorkspaceStatus.IDLE,
            AgentActivityState.WORKING,
            True,
            "grove_launched",
            True,
            AgentActivityState.WORKING,
        ),
    ],
)
def test_blend_truth_table(
    ws_status: WorkspaceStatus,
    transcript_state: AgentActivityState,
    has_transcript: bool,
    provenance: str,
    remote: bool,
    expected: AgentActivityState,
) -> None:
    transcript = AgentActivity(state=transcript_state)
    assert (
        ActivityService._blend(
            ws_status,
            transcript,
            has_transcript=has_transcript,
            provenance=provenance,
            remote=remote,
            now=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
        )
        is expected
    )


def test_blend_fresh_transcript_outranks_quiet_pane() -> None:
    """The adapter's abstraction wins over the tmux heuristic: a WORKING
    transcript that advanced recently stays WORKING through a quiet pane (a
    thinking/long-tool agent emits no output — demoting on the pane alone was
    the flaky WORKING→IDLE flapping). Only when the transcript itself has gone
    stale does the quiet pane demote to IDLE (the killed-mid-tool case)."""
    now = datetime(2026, 6, 1, 10, 10, 0, tzinfo=UTC)

    def blend(last_event_at: datetime | None) -> AgentActivityState:
        return ActivityService._blend(
            WorkspaceStatus.IDLE,
            AgentActivity(state=AgentActivityState.WORKING, last_event_at=last_event_at),
            has_transcript=True,
            provenance="grove_launched",
            remote=False,
            now=now,
        )

    assert blend(now - timedelta(seconds=30)) is AgentActivityState.WORKING
    assert blend(now - timedelta(seconds=3600)) is AgentActivityState.IDLE
    assert blend(None) is AgentActivityState.IDLE


def test_sessions_for_promotes_orchestrator_waiting_to_working_via_fleet(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Integration proof at the seam every consumer of ``sessions_for`` shares
    (daemon poll, TUI list tick): an orchestrator whose tail assistant reply
    closed its OWN turn (``end_turn``) while a BACKGROUNDED sub-agent it just
    spawned is still running reads WORKING, not the stale WAITING a bare
    ``end_turn`` tail otherwise produces (``test_snapshot_parses_real_transcript``
    pins that exact WAITING result for the same create()+tmux conditions minus
    the active fleet). ``_blend`` itself never consults ``active_subagents`` —
    the promotion happens upstream inside the adapter's own ``activity()``, so
    this is provable only by going through the real ``sessions_for`` seam."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    created = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="orchestrator"))
    worktree = Path(created.worktree_path)
    sid = created.agent_session_id
    assert sid is not None
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"find the flaky test"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"tool_use",'
        '"content":[{"type":"tool_use","id":"tu1","name":"Agent",'
        '"input":{"description":"Explore","subagent_type":"Explore","prompt":"go",'
        '"run_in_background":true}}]}}\n'
        '{"type":"user","uuid":"t1","timestamp":"2026-06-01T10:00:02.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":'
        '[{"type":"tool_result","tool_use_id":"tu1","content":"Async agent launched"}]}}\n'
        '{"type":"assistant","uuid":"a2","requestId":"r2","timestamp":"2026-06-01T10:00:03.000Z",'
        '"isSidechain":false,"message":{"id":"m2","role":"assistant","stop_reason":"end_turn",'
        '"content":[{"type":"text","text":"Kicked off a background exploration."}]}}\n',
        encoding="utf-8",
    )

    # `sessions_for` takes an already-RECONCILED state (`WorkspaceStatus.ACTIVE`/
    # `IDLE`, computed at read time) — the same object every real caller
    # (daemon poll, TUI list tick) holds via `mgr.list()`. The bare `create()`
    # return value still carries the PERSISTED `RUNNING` status.
    state = next(s for s in mgr.list() if s.id == created.id)
    sessions = service.sessions_for(mgr, state)
    primary = sessions[0]
    assert primary.session.session_id == sid
    assert primary.activity.active_subagents == 1
    assert primary.activity.state is AgentActivityState.WORKING


# ─── snapshot ───────────────────────────────────────────────────────────────


def test_snapshot_groups_across_repos(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo_a = _init_repo(tmp_path / "repo_a")
    repo_b = _init_repo(tmp_path / "repo_b")
    registry.get(repo_a).create(CreateWorkspaceRequest(agent_name="claude", title="a-task"))
    registry.get(repo_b).create(CreateWorkspaceRequest(agent_name="shell", title="b-task"))

    snap = service.snapshot()

    assert snap.total_workspaces == 2
    names = {g.repo_name for g in snap.projects}
    assert names == {"repo_a", "repo_b"}
    # The shell workspace tracks no session; the claude one has one (STARTING — no
    # transcript on disk yet).
    by_name = {g.repo_name: g for g in snap.projects}
    claude_row = by_name["repo_a"].workspaces[0]
    shell_row = by_name["repo_b"].workspaces[0]
    assert len(claude_row.sessions) == 1
    assert claude_row.primary is not None
    assert claude_row.primary.state is AgentActivityState.STARTING
    assert shell_row.sessions == ()


def test_snapshot_parses_real_transcript(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="track"))
    worktree = Path(state.worktree_path)
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"do the thing"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","model":"claude-opus-4-8",'
        '"stop_reason":"end_turn","usage":{"input_tokens":10,"output_tokens":2},'
        '"content":[{"type":"text","text":"done"}]}}\n',
        encoding="utf-8",
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert primary.human_turns == 1
    assert primary.current_task == "do the thing"
    # has_transcript True + transcript WAITING (end_turn) → WAITING.
    assert primary.state is AgentActivityState.WAITING


def test_snapshot_itemizes_fleet_and_excludes_it_from_attention(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The dashboard card's ``sessions`` list itemizes a sub-agent fleet member
    alongside the primary — not just a bare ``active_subagents`` int — with
    the parent/child link set. A finished sub-agent settles to WAITING
    (an ``ATTENTION_STATE`` for a real human-facing session) but must NOT bubble
    into the workspace's own ``needs_attention``: it is itemized detail, not a
    second conversation waiting on the human."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="fleet"))
    worktree = Path(state.worktree_path)
    sid = state.agent_session_id
    assert sid is not None
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    # Primary is still mid tool-loop (spawned an Agent, no result yet) → WORKING,
    # never an attention state — so any attention-flagging here must come from
    # a fleet entry incorrectly bubbling up if the exclusion regresses.
    (folder / f"{sid}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"fan out"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"tool_use",'
        '"content":[{"type":"tool_use","id":"tu1","name":"Agent",'
        '"input":{"description":"Explore it","subagent_type":"Explore"}}]}}\n',
        encoding="utf-8",
    )
    sub_dir = folder / sid / "subagents"
    sub_dir.mkdir(parents=True)
    (sub_dir / "agent-a1.jsonl").write_text(
        '{"type":"user","uuid":"su1","isSidechain":true,"agentId":"a1",'
        '"timestamp":"2026-06-01T10:00:02.000Z","message":{"role":"user","content":"go explore"}}\n'
        '{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"a1",'
        '"timestamp":"2026-06-01T10:00:03.000Z","message":{"id":"sm1","role":"assistant",'
        '"model":"claude-haiku-4-5-20251001","stop_reason":"end_turn",'
        '"content":[{"type":"text","text":"Found it."}]}}\n',
        encoding="utf-8",
    )
    (sub_dir / "agent-a1.meta.json").write_text(
        '{"agentType":"Explore","description":"Explore it","toolUseId":"tu1"}', encoding="utf-8"
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 2
    primary, fleet_member = row.sessions
    assert primary.session.session_id == sid
    assert primary.session.parent_session_id is None
    assert primary.activity.active_subagents == 1  # unchanged derived count
    assert primary.activity.state is AgentActivityState.WORKING

    assert fleet_member.session.session_id == "a1"
    assert fleet_member.session.parent_session_id == sid
    assert fleet_member.activity.title == "Explore"
    assert fleet_member.activity.state is AgentActivityState.WAITING

    assert row.needs_attention is False


def test_snapshot_never_calls_peek(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The list path uses the cheap diff_stats, never the expensive peek/full-diff."""
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="cheap"))
    monkeypatch.setattr(mgr, "peek", lambda *a, **k: pytest.fail("snapshot must not call peek()"))

    row = service.snapshot().projects[0].workspaces[0]
    assert isinstance(row.diff_added, int)
    assert isinstance(row.diff_removed, int)


def test_poll_once_does_not_redundantly_reconcile_pane_target(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`_workspace_activity` must resolve the pane target from the `WorkspaceState`
    `list()` already reconciled this tick, not re-fetch the raw state and run
    `_reconcile_status` a second time through the id-only `pane_target()`.

    The redundant pass alone doubles `has_session`/`pane_activity_seconds_ago`
    and triples `list_windows` for every RUNNING workspace, every poll — at
    fleet scale (24 workspaces) that's ~100 avoidable tmux forks per tick on
    top of the ones reconciliation legitimately needs.
    """
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    mgr.create(CreateWorkspaceRequest(agent_name="claude", title="fork-count"))

    calls = {"has_session": 0, "list_windows": 0, "pane_activity_seconds_ago": 0}

    def _counted(name: str) -> Callable[..., Any]:
        real = getattr(tmux, name)

        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            calls[name] += 1
            return real(*args, **kwargs)

        return _wrapped

    for name in calls:
        monkeypatch.setattr(tmux, name, _counted(name))

    service.poll_once()

    # One reconcile's worth per workspace: `list()` reconciles status once
    # (has_session + list_windows + pane_activity_seconds_ago), and resolving
    # the pane target for the ActivityService row costs exactly one more
    # `list_windows` call (the target itself, not a second reconciliation).
    assert calls == {"has_session": 1, "list_windows": 2, "pane_activity_seconds_ago": 1}


# ─── delta bus ──────────────────────────────────────────────────────────────


def test_poll_emits_session_activity_delta_on_change(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="poll"))
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    transcript = folder / f"{state.agent_session_id}.jsonl"

    deltas: list[DashboardDelta] = []
    service.subscribe(deltas.append)

    service.poll_once()  # primes fingerprints (STARTING, no file)
    deltas.clear()

    # Activity changes: a transcript appears → STARTING → WAITING.
    transcript.write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
        '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"text","text":"k"}]}}\n',
        encoding="utf-8",
    )
    service.poll_once()

    activity_deltas = [d for d in deltas if d.kind == "session_activity"]
    assert len(activity_deltas) == 1
    assert activity_deltas[0].workspace_id == state.id
    assert activity_deltas[0].workspace is not None
    assert activity_deltas[0].workspace.primary is not None
    assert activity_deltas[0].workspace.primary.state is AgentActivityState.WAITING

    # No further change → no new delta.
    deltas.clear()
    service.poll_once()
    assert [d for d in deltas if d.kind == "session_activity"] == []


def test_lifecycle_event_bridged_to_workspace_changed(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="bridge"))

    deltas: list[DashboardDelta] = []
    service.subscribe(deltas.append)  # bridges the now-known repo's manager bus

    mgr.update(state.id, title="renamed")

    changed = [d for d in deltas if d.kind == "workspace_changed"]
    assert any(d.workspace_id == state.id and d.detail.get("event") == "updated" for d in changed)


def test_unsubscribe_is_idempotent(env: tuple[ActivityService, RepoRegistry]) -> None:
    service, _ = env
    unsub = service.subscribe(lambda _d: None)
    unsub()
    unsub()  # second call must not raise


# ─── wire views ─────────────────────────────────────────────────────────────


def test_snapshot_view_serializes(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="view"))

    view = DashboardSnapshotView.from_snapshot(service.snapshot())
    payload = view.model_dump_json()
    assert '"total_workspaces":1' in payload
    assert view.projects[0].workspaces[0].state.title == "view"


def test_event_from_delta_round_trips(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="ev"))
    row = service.snapshot().projects[0].workspaces[0]
    delta = DashboardDelta(
        kind="session_activity", seq=7, workspace_id=state.id, repo_root=str(repo), workspace=row
    )
    event = DashboardEvent.from_delta(delta)
    assert event.kind == "session_activity"
    assert event.seq == 7
    assert event.workspace is not None
    assert event.workspace.state.id == state.id


# ─── push-status sidecar + out-of-band discovery ────────────────────────────


def test_sidecar_overrides_polled_state(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="blk"))

    # No transcript on disk → polled blend would be STARTING. A fresh hook sidecar
    # for a permission prompt wins: the dashboard shows BLOCKED (polling can't see it).
    ClaudeHook.record_event(
        {"hook_event_name": "Notification", "session_id": state.agent_session_id},
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )
    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert primary.state is AgentActivityState.BLOCKED


# ─── live pending question ───────────────────────────────────────────────────


def _ask_capture(sidecar_dir: Path, session_id: str | None, *, now: datetime) -> None:
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_1",
            "tool_input": {
                "questions": [
                    {
                        "question": "Pick a color",
                        "header": "Color",
                        "multiSelect": False,
                        "options": [{"label": "Blue"}, {"label": "Green"}],
                    }
                ]
            },
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=now,
    )


def test_live_question_surfaces_on_activity_view(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A question captured at ask-time rides the activity view immediately —
    before Claude Code flushes anything to the transcript."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    _ask_capture(sidecar_dir, state.agent_session_id, now=datetime.now(tz=UTC))

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert len(primary.questions) == 1
    q = primary.questions[0]
    assert q.prompt == "Pick a color"
    assert q.group_id == "toolu_1"  # the answer-back tool_use_id
    assert [o.label for o in q.options] == ["Blue", "Green"]


def test_live_question_surfaces_whole_batch_in_order(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """One AskUserQuestion call carries up to four questions answered atomically,
    so the whole group rides together, ordered as asked."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": state.agent_session_id,
            "tool_name": "AskUserQuestion",
            "tool_use_id": "toolu_1",
            "tool_input": {
                "questions": [
                    {"question": "Color?", "options": [{"label": "Blue"}]},
                    {"question": "Toppings?", "multiSelect": True, "options": [{"label": "A"}]},
                ]
            },
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert [q.prompt for q in primary.questions] == ["Color?", "Toppings?"]
    # Same batch → shared group_id (the answer-back tool_use_id); distinct ids.
    assert {q.group_id for q in primary.questions} == {"toolu_1"}
    assert [q.id for q in primary.questions] == ["toolu_1#0", "toolu_1#1"]


def test_live_question_suppressed_once_transcript_resolves_it(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Once the transcript carries the resolving tool_result for the captured
    tool_use_id (the flush after an answer/cancel), the pending question is not
    exposed — even though the sidecar still holds the capture."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="q"))
    # Capture asked at 10:00:00; the transcript then flushes the tool_use + its
    # resolving tool_result at 10:00:05/06 (after the ask) — so the cross-check
    # fires and finds the resolution.
    _ask_capture(
        sidecar_dir, state.agent_session_id, now=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC)
    )
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:05.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"tool_use",'
        '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"tool_use",'
        '"id":"toolu_1","name":"AskUserQuestion","input":{"questions":[{"question":"Pick a color",'
        '"options":[{"label":"Blue"}]}]}}]}}\n'
        '{"type":"user","uuid":"u2","timestamp":"2026-06-01T10:00:06.000Z","isSidechain":false,'
        '"message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1",'
        '"content":"Your questions have been answered"}]}}\n',
        encoding="utf-8",
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    assert primary.questions == ()


def test_sidecar_superseded_by_newer_transcript(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A Stop sidecar (WAITING) written BEFORE the transcript's last record is
    stale — the steered agent's polled state must win, not pin WAITING."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="steer"))

    # Push (Stop → WAITING) at 10:00:00; transcript then advances at 10:00:05.
    ClaudeHook.record_event(
        {"hook_event_name": "Stop", "session_id": state.agent_session_id},
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC),
    )
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    (folder / f"{state.agent_session_id}.jsonl").write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:05.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"follow-up"}}\n',
        encoding="utf-8",
    )

    primary = service.snapshot().projects[0].workspaces[0].primary
    assert primary is not None
    # Human-turn tail → transcript WORKING; tmux quiet (FakeTmux) → polled IDLE.
    # The point: NOT the sidecar's WAITING.
    assert primary.state is not AgentActivityState.WAITING


def test_degraded_read_keeps_last_definitive_state(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Hysteresis: once a session has shown a definitive state, a transient
    collapsed read (vanished/unreadable transcript → would-be STARTING) keeps
    the last definitive state instead of flashing the card back to starting."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="hys"))
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(Path(state.worktree_path))
    folder.mkdir(parents=True)
    transcript = folder / f"{state.agent_session_id}.jsonl"
    transcript.write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-06-01T10:00:00.000Z",'
        '"isSidechain":false,"message":{"role":"user","content":"go"}}\n'
        '{"type":"assistant","uuid":"a1","requestId":"r1","timestamp":"2026-06-01T10:00:01.000Z",'
        '"isSidechain":false,"message":{"id":"m1","role":"assistant","stop_reason":"end_turn",'
        '"usage":{"input_tokens":1,"output_tokens":1},"content":[{"type":"text","text":"k"}]}}\n',
        encoding="utf-8",
    )

    first = service.snapshot().projects[0].workspaces[0].primary
    assert first is not None and first.state is AgentActivityState.WAITING

    transcript.unlink()  # the transient collapse (mid-rotation / racing read)
    second = service.snapshot().projects[0].workspaces[0].primary
    assert second is not None
    assert second.state is AgentActivityState.WAITING  # not STARTING


def test_settled_working_expires_into_honest_starting(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A settled WORKING ages out on the sidecar window (the dead-agent guard).

    With hooks on, SessionStart settles WORKING before any transcript exists; a
    workspace whose agent dies at boot — or is never prompted — must fall back
    to the honest degraded state once the window passes, not read WORKING
    forever from the hysteresis cache.
    """
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="dead"))

    t0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": state.agent_session_id},
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=t0,
    )
    sid = state.agent_session_id
    assert sid is not None
    mgr = registry.get(repo)

    def state_at(now: datetime) -> AgentActivityState:
        sidecar = ClaudeHook.read(sid, sidecar_dir=sidecar_dir)
        return service._session_activity(
            mgr,
            state,
            "claude_code",
            sid,
            "grove_launched",
            now,
            sidecar=sidecar,
            cwd=state.agent_cwd,
        ).activity.state

    # Tick 1 (within the sidecar window): the push wins, WORKING settles.
    assert state_at(t0) is AgentActivityState.WORKING
    # Tick 2 (past the window, still no transcript): the cached WORKING must
    # expire rather than answer the degraded read forever.
    assert state_at(t0 + timedelta(seconds=600)) is AgentActivityState.STARTING


def test_fingerprint_covers_every_session_not_just_primary() -> None:
    """A secondary session's state change — and the sessions set going empty —
    must change the fingerprint, or hand-started sessions and discovery misses
    never stream."""
    t0 = datetime(2026, 6, 1, tzinfo=UTC)

    def row(sessions: tuple[SessionActivity, ...]) -> WorkspaceActivity:
        return WorkspaceActivity(
            state=WorkspaceState(
                id="w1",
                title="t",
                repo_root="/r",
                branch="b",
                base_branch="main",
                worktree_path="/w",
                tmux_session="s",
                agent_name="claude",
                status=WorkspaceStatus.RUNNING,
                created_at=t0,
                updated_at=t0,
            ),
            sessions=sessions,
            base_ahead=0,
            base_behind=0,
            diff_added=0,
            diff_removed=0,
            dirty_files=0,
            pane_target=None,
            recent_commits=(),
            observed_at=datetime(2026, 6, 1, tzinfo=UTC),
        )

    def sess(sid: str, st: AgentActivityState) -> SessionActivity:
        return SessionActivity(
            session=AgentSession(
                session_id=sid,
                transcript_path=None,
                adapter_kind="claude_code",
                provenance="fs_discovered",
            ),
            activity=AgentActivity(state=st),
        )

    primary = sess("a", AgentActivityState.WORKING)
    two = row((primary, sess("b", AgentActivityState.WORKING)))
    two_changed = row((primary, sess("b", AgentActivityState.WAITING)))
    assert two.fingerprint != two_changed.fingerprint  # secondary streams
    assert row(()).fingerprint != two.fingerprint  # emptied set streams


def test_fs_discovery_surfaces_handstarted_session(
    fake_tmux: FakeTmux, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del fake_tmux
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    cfg = GroveConfig.model_validate(
        {"tmux": {"session_prefix": "test-"}, "hooks": {"enabled": True}}
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    service = ActivityService(registry=registry)

    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="disc"))
    worktree = Path(state.worktree_path)
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    # A session the user started by hand in the worktree CONCURRENTLY — same cwd,
    # different id, born after the workspace so the birth gate adopts it (#F5: a
    # transcript predating the workspace is excluded as history, tested below).
    born_after = _iso(state.created_at + timedelta(seconds=5))
    (folder / "99999999-9999-4999-8999-999999999999.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born_after}","isSidechain":false,'
        '"message":{"role":"user","content":"hand started"}}\n',
        encoding="utf-8",
    )

    sessions = service.snapshot().projects[0].workspaces[0].sessions
    provenances = {s.session.provenance for s in sessions}
    assert "grove_launched" in provenances
    assert "fs_discovered" in provenances
    assert len(sessions) == 2


def test_null_session_id_recovered_by_discovery(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A claude_code workspace with no minted ``agent_session_id`` (a legacy
    record, or an agent configured without ``kind="claude_code"`` so creation
    never minted one) still surfaces its live session via out-of-band discovery.

    This is the root cause of the dashboard's "unknown / no agent session"
    state: ``sessions_for`` used to early-return ``[]`` whenever the id was
    falsy, blanking the whole agent axis even though a real transcript existed
    on disk for the worktree.
    """
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="legacy"))
    worktree = Path(state.worktree_path)
    # The broken on-disk shape: persisted with no minted id (no hooks needed).
    mgr.store.save(replace(state, agent_session_id=None))

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / "11111111-1111-4111-8111-111111111111.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"recover me"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.primary is not None
    assert row.sessions[0].session.provenance == "fs_discovered"
    assert row.primary.current_task == "recover me"


def test_unmaterialized_minted_id_yields_primary_to_discovered_session(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A minted id whose transcript never materialized must not pin the
    workspace on STARTING forever: an in-process rotation (``/clear`` mints a
    NEW session id inside the same claude) leaves the live session under a
    different id in the same cwd. The newest discovered session takes the
    primary slot (ungated by hooks); the minted entry stays behind it so it
    takes back over if it ever materializes."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="rotated"))
    worktree = Path(state.worktree_path)
    # The minted id exists on the record but no transcript was ever written
    # for it; the post-/clear session lives under a different id in this cwd.
    live_sid = "deadbeef-0000-4000-8000-000000000000"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / f"{live_sid}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"the live session"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert [s.session.provenance for s in row.sessions] == ["fs_discovered", "grove_launched"]
    assert row.sessions[0].session.session_id == live_sid
    assert row.primary is not None
    assert row.primary.current_task == "the live session"
    assert row.primary.state is not AgentActivityState.STARTING


def test_create_persists_agent_kind(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """create() captures the resolved agent's kind on the record, so the
    dashboard can resolve the adapter without re-reading (possibly repo-scoped)
    config."""
    _, registry = env
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    assert mgr.create(CreateWorkspaceRequest(agent_name="claude", title="k")).agent_kind == (
        "claude_code"
    )
    assert mgr.create(CreateWorkspaceRequest(agent_name="shell", title="s")).agent_kind == (
        "generic"
    )


def test_persisted_agent_kind_resolves_when_name_absent_from_config(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The dashboard resolves a workspace's adapter from the persisted
    ``agent_kind``, NOT the live config — so an agent scoped to a repo's project
    config (invisible to the daemon's global config) still surfaces its sessions.
    This is what lets the 'Work' profile live only in private-repos while
    the daemon dashboard keeps resolving those workspaces."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="scoped"))
    worktree = Path(state.worktree_path)
    # Agent name is NOT in the daemon's config; only the persisted kind says claude_code.
    mgr.store.save(
        replace(
            state,
            agent_name="Claude Code (Work)",
            agent_session_id=None,
            agent_kind="claude_code",
        )
    )

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / "abcdef00-0000-4000-8000-000000000000.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"scoped session"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.primary is not None
    assert row.primary.current_task == "scoped session"


def test_null_id_discovery_adopts_single_most_recent(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With no minted id and several transcripts for the worktree, exactly one
    session (the most recently active, among those adopted) is surfaced —
    never the worktree's whole history. Discovery orders newest-first; the
    null-id path walks that order for the first one `adopts_session` accepts."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="multi"))
    worktree = Path(state.worktree_path)
    mgr.store.save(replace(state, agent_session_id=None))

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    newest = "33333333-3333-4333-8333-333333333333"
    for sid, mtime in (("22222222-2222-4222-8222-222222222222", 1000), (newest, 2000)):
        path = folder / f"{sid}.jsonl"
        path.write_text(
            f'{{"type":"user","uuid":"u-{sid[:4]}","cwd":"{worktree}",'
            f'"timestamp":"{born}","isSidechain":false,'
            f'"message":{{"role":"user","content":"task {sid[:4]}"}}}}\n',
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    row = service.snapshot().projects[0].workspaces[0]
    assert len(row.sessions) == 1
    assert row.sessions[0].session.session_id == newest


def test_root_workspace_never_promotes_stale_repo_root_session_to_primary(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The bug report's exact shape: a ROOT-placement workspace's cwd IS the
    shared repo root, which may already hold a much older session's transcript
    from before this workspace existed. The dashboard must not present that
    stale transcript as this brand-new workspace's live session (honest
    STARTING instead) — it still rides along as a non-primary member so it
    isn't silently dropped from the fingerprint."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")

    stale_sid = "88888888-8888-4888-8888-888888888888"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(repo)
    folder.mkdir(parents=True)
    path = folder / f"{stale_sid}.jsonl"
    path.write_text(
        f'{{"type":"user","uuid":"u","cwd":"{repo}",'
        '"timestamp":"2020-01-01T00:00:00.000Z","isSidechain":false,'
        '"message":{"role":"user","content":"leftover from a prior life"}}\n',
        encoding="utf-8",
    )
    os.utime(path, (99_999_999, 99_999_999))  # newest mtime around — the bug's trigger

    state = registry.get(repo).create(
        CreateWorkspaceRequest(agent_name="claude", title="fresh root", branch_plan=RootBranch())
    )
    assert Path(state.worktree_path).resolve() == repo.resolve()

    row = service.snapshot().projects[0].workspaces[0]
    assert row.primary is not None
    assert row.primary.state is AgentActivityState.STARTING
    assert row.primary.current_task != "leftover from a prior life"
    assert row.sessions[0].session.session_id != stale_sid


# ─── dead-pointer recovery + resumed-session sidecar adoption ───────────────


def test_sessionend_dead_pointer_recovers_resumed_session(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """End to end: Grove minted M and launched ``claude --session-id M``, but
    the user resumed a pre-existing session R in the pane. M ends almost
    immediately — its OWN SessionEnd sidecar settles the blend to IDLE (not
    STARTING/UNKNOWN), so a dead pointer must not read as live and skip
    recovery. R was born long before this workspace, so birth alone can never
    adopt it — but its post-create SessionStart sidecar in this cwd does. R
    must become primary; the dead M rides behind."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="resumed"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None

    # M ends 7s after create — SessionEnd settles the blend to IDLE.
    ClaudeHook.record_event(
        {"hook_event_name": "SessionEnd", "session_id": minted, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%479",
        now=state.created_at + timedelta(seconds=7),
    )
    # R: resumed in the pane, born 23 min BEFORE this workspace, but live here
    # now (fresh transcript + a post-create SessionStart sidecar in this cwd).
    resumed = "deadbeef-0000-4000-8000-000000000000"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born_before = _iso(state.created_at - timedelta(minutes=23))
    (folder / f"{resumed}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born_before}","isSidechain":false,'
        '"message":{"role":"user","content":"resumed conversation"}}\n',
        encoding="utf-8",
    )
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": resumed, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%479",
        now=state.created_at + timedelta(seconds=8),
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert row.primary is not None
    assert row.sessions[0].session.session_id == resumed
    assert row.sessions[0].session.provenance == "fs_discovered"
    assert row.primary.current_task == "resumed conversation"
    assert row.primary.state is not AgentActivityState.STARTING
    # The dead minted pointer rides behind rather than being dropped.
    assert row.sessions[-1].session.session_id == minted


def test_resumed_session_adopted_via_sidecar_evidence(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A minted --session-id went dead (user /resume'd in the pane), and the
    resumed session — born BEFORE the workspace — is adopted because its
    post-create sidecar shares the SAME pane the minted session's sidecar
    recorded (the reference pane). Birth alone rejected it; pane-verified
    live-here evidence carries it, and the dead minted entry yields the
    primary slot."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="resumed"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None

    # The minted id is a dead pointer: SessionEnd, no transcript — its sidecar
    # pins the workspace's reference pane (%12).
    ClaudeHook.record_event(
        {"hook_event_name": "SessionEnd", "session_id": minted, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%12",
        now=state.created_at + timedelta(seconds=2),
    )

    resumed = "abcabc00-0000-4000-8000-000000000000"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born_before = _iso(state.created_at - timedelta(minutes=30))
    (folder / f"{resumed}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{born_before}","isSidechain":false,'
        '"message":{"role":"user","content":"resumed here"}}\n',
        encoding="utf-8",
    )
    # The resumed session is live in the SAME pane %12 after creation.
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": resumed, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%12",
        now=state.created_at + timedelta(seconds=5),
    )

    row = service.snapshot().projects[0].workspaces[0]
    ids = [s.session.session_id for s in row.sessions]
    assert ids[0] == resumed  # recovery promotes the resumed session to primary
    assert minted in ids  # the dead minted entry rides behind
    assert row.primary is not None
    assert row.primary.current_task == "resumed here"


def test_cross_tenant_live_session_rejected_on_pane_mismatch(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A workspace must NOT adopt another live workspace's session that
    happens to share its cwd (the ROOT-placement hole). The other tenant's
    sidecar keeps refreshing ``ts >= created_at`` with the same cwd, but its pane
    differs from THIS workspace's reference pane (the minted session's), so the
    live-here evidence is rejected and only our own session stands."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="tenant-a"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)

    # Our own materialized session, its sidecar pinning the reference pane %1.
    mine_born = _iso(state.created_at + timedelta(seconds=1))
    (folder / f"{minted}.jsonl").write_text(
        f'{{"type":"user","uuid":"m","cwd":"{worktree}","timestamp":"{mine_born}",'
        '"isSidechain":false,"message":{"role":"user","content":"mine"}}\n',
        encoding="utf-8",
    )
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": minted, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%1",
        now=state.created_at + timedelta(seconds=1),
    )
    # A DIFFERENT live workspace's session, sharing this cwd, refreshing after our
    # creation — but in pane %2. Born before us, so only pane-live could adopt it.
    tenant_b = "bbbbbbbb-0000-4000-8000-000000000000"
    (folder / f"{tenant_b}.jsonl").write_text(
        f'{{"type":"user","uuid":"b","cwd":"{worktree}",'
        f'"timestamp":"{_iso(state.created_at - timedelta(hours=1))}",'
        '"isSidechain":false,"message":{"role":"user","content":"theirs"}}\n',
        encoding="utf-8",
    )
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": tenant_b, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%2",
        now=state.created_at + timedelta(seconds=5),
    )

    ids = [s.session.session_id for s in service.snapshot().projects[0].workspaces[0].sessions]
    assert minted in ids
    assert tenant_b not in ids  # pane mismatch → not ours


def test_minted_card_excludes_historical_session(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A discovered session predating the workspace (no live-here sidecar) is
    excluded from the card entirely — history is rejected on cheap birth metadata
    before any full parse, so per-tick cost stays O(new sessions)."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="hist"))
    worktree = Path(state.worktree_path)
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born_before = _iso(state.created_at - timedelta(days=3))
    (folder / "11110000-0000-4000-8000-000000000000.jsonl").write_text(
        f'{{"type":"user","uuid":"h","cwd":"{worktree}","timestamp":"{born_before}",'
        '"isSidechain":false,"message":{"role":"user","content":"old work"}}\n',
        encoding="utf-8",
    )

    sessions = service.snapshot().projects[0].workspaces[0].sessions
    ids = [s.session.session_id for s in sessions]
    assert ids == [state.agent_session_id]  # only the minted session; history excluded


def test_historical_sessions_never_full_parsed(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The O(new sessions) cost guarantee: a full ``parse_activity`` is paid
    for the minted session and any ADOPTED candidate only; historical transcripts
    are rejected on the cheap birth head-read (``discover_births``) and never
    full-parsed, so per-tick cost is O(new), not O(history)."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="cost"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    (folder / f"{minted}.jsonl").write_text(
        f'{{"type":"user","uuid":"m","cwd":"{worktree}",'
        f'"timestamp":"{_iso(state.created_at + timedelta(seconds=1))}",'
        '"isSidechain":false,"message":{"role":"user","content":"mine"}}\n',
        encoding="utf-8",
    )
    historical = [f"{n}0000000-0000-4000-8000-000000000000" for n in range(1, 5)]
    for hid in historical:
        (folder / f"{hid}.jsonl").write_text(
            f'{{"type":"user","uuid":"h","cwd":"{worktree}",'
            f'"timestamp":"{_iso(state.created_at - timedelta(days=2))}",'
            '"isSidechain":false,"message":{"role":"user","content":"old"}}\n',
            encoding="utf-8",
        )

    parsed: list[str] = []
    real_parse = ClaudeCodeAdapter.parse_activity

    def spy(self: ClaudeCodeAdapter, cwd: Path, session_id: str) -> object:
        parsed.append(session_id)
        return real_parse(self, cwd, session_id)

    monkeypatch.setattr(ClaudeCodeAdapter, "parse_activity", spy)
    service.snapshot()

    assert minted in parsed
    for hid in historical:
        assert hid not in parsed  # cheap birth reject — no full parse for history


def test_materialized_sessionend_stays_primary(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A minted session that HAS a transcript is never a dead pointer, even
    when its last hook event is SessionEnd — a remapped/materialized ended session
    stays primary showing its honest idle/done state, not demoted below a
    discovered bystander."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="ended"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    # The minted session materialized, then ended (SessionEnd sidecar).
    mine_born = _iso(state.created_at + timedelta(seconds=1))
    (folder / f"{minted}.jsonl").write_text(
        f'{{"type":"user","uuid":"m","cwd":"{worktree}","timestamp":"{mine_born}",'
        '"isSidechain":false,"message":{"role":"user","content":"my work"}}\n',
        encoding="utf-8",
    )
    ClaudeHook.record_event(
        {"hook_event_name": "SessionEnd", "session_id": minted, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%1",
        now=state.created_at + timedelta(seconds=3),
    )
    # A concurrent discovered session that WOULD be adoptable (born after us).
    bystander = "cccc0000-0000-4000-8000-000000000000"
    (folder / f"{bystander}.jsonl").write_text(
        f'{{"type":"user","uuid":"c","cwd":"{worktree}",'
        f'"timestamp":"{_iso(state.created_at + timedelta(seconds=2))}",'
        '"isSidechain":false,"message":{"role":"user","content":"bystander"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert row.sessions[0].session.session_id == minted  # materialized end stays primary
    assert bystander in [s.session.session_id for s in row.sessions]  # bystander rides behind


def test_stale_cwd_session_with_predating_sidecar_not_adopted(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The stale-cwd protection survives the sidecar-evidence extension: a
    previous tenant of a reused cwd left both a transcript AND a sidecar, but
    both predate this workspace's creation, so the ``>= created_at`` guard still
    rejects it. (Pane evidence would also reject on mismatch; cwd+ts is the
    accepted fallback and the ts guard is what holds here.)"""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="reused"))
    worktree = Path(state.worktree_path)
    mgr.store.save(replace(state, agent_session_id=None))

    stale = "99998888-7777-4666-8555-444433332222"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    predating = _iso(state.created_at - timedelta(days=2))
    (folder / f"{stale}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{predating}","isSidechain":false,'
        '"message":{"role":"user","content":"prior tenant"}}\n',
        encoding="utf-8",
    )
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": stale, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%1",
        now=state.created_at - timedelta(days=2),  # sidecar predates create too
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert row.sessions == ()  # nothing adopted — the prior tenant is not ours


def test_sessionend_before_transcript_keeps_minted_materialized(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The dead-pointer SessionEnd check is gated on the sidecar still
    superseding the poll: when the minted session's transcript advanced PAST its
    SessionEnd (the id continued/rematerialized), the minted entry stays primary
    — preserving 'the minted entry reclaims primary the moment it materializes'
    even though a discovered bystander would otherwise be adoptable."""
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="rematerialized"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None

    ClaudeHook.record_event(
        {"hook_event_name": "SessionEnd", "session_id": minted, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%1",
        now=state.created_at + timedelta(seconds=5),
    )
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    later = _iso(state.created_at + timedelta(seconds=10))  # transcript outran SessionEnd
    (folder / f"{minted}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{worktree}",'
        f'"timestamp":"{later}","isSidechain":false,'
        '"message":{"role":"user","content":"still going"}}\n',
        encoding="utf-8",
    )
    other = "12341234-0000-4000-8000-000000000000"
    (folder / f"{other}.jsonl").write_text(
        f'{{"type":"user","uuid":"o","cwd":"{worktree}",'
        f'"timestamp":"{later}","isSidechain":false,'
        '"message":{"role":"user","content":"a bystander"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    assert row.primary is not None
    assert row.sessions[0].session.session_id == minted  # minted stayed primary
    assert row.primary.current_task == "still going"


# ─── nested-project discovery (agent_cwd, not worktree root) ────────────────


def test_nested_project_discovery_scans_agent_cwd(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A nested project's agent runs in worktree/subpath and records its
    transcript's cwd there — discovery must scan agent_cwd, not the worktree
    root, or the session is invisible."""
    service, registry = env
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    sub = repo / "services" / "api"
    sub.mkdir(parents=True)
    (sub / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "api", "--no-verify"], cwd=repo, check=True, capture_output=True
    )

    mgr = registry.get(repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="nested", project_cwd=sub))
    assert state.project_subpath == "services/api"
    agent_cwd = state.agent_cwd

    handstarted = "aaaa1111-2222-4333-8444-555566667777"
    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(agent_cwd)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    (folder / f"{handstarted}.jsonl").write_text(
        f'{{"type":"user","uuid":"u","cwd":"{agent_cwd}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"nested work"}}\n',
        encoding="utf-8",
    )

    rows = list(service.snapshot().iter_workspaces())
    assert len(rows) == 1
    by_id = {s.session.session_id: s for s in rows[0].sessions}
    assert handstarted in by_id
    assert by_id[handstarted].activity.current_task == "nested work"


# ─── extras discovery is not gated by cfg.hooks.enabled ─────────────────────


def test_extras_discovered_without_hooks_enabled(
    env: tuple[ActivityService, RepoRegistry],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent hand-started sessions in a workspace's cwd surface even with
    hooks DISABLED — discovery is a read-only fs glob, not gated on
    cfg.hooks.enabled. hooks.enabled gates only the sidecar push."""
    service, registry = env  # env's cfg leaves hooks disabled (the default)
    cfg_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    assert mgr.config.hooks.enabled is False
    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="extras"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None

    folder = cfg_home / "projects" / _ClaudeHome.encode_cwd(worktree)
    folder.mkdir(parents=True)
    born = _iso(state.created_at + timedelta(seconds=1))
    # The Grove-minted session materialized (so it stays primary, not recovered).
    (folder / f"{minted}.jsonl").write_text(
        f'{{"type":"user","uuid":"m","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"minted work"}}\n',
        encoding="utf-8",
    )
    hand = "beadfeed-0000-4000-8000-000000000000"
    (folder / f"{hand}.jsonl").write_text(
        f'{{"type":"user","uuid":"h","cwd":"{worktree}",'
        f'"timestamp":"{born}","isSidechain":false,'
        '"message":{"role":"user","content":"hand started"}}\n',
        encoding="utf-8",
    )

    row = service.snapshot().projects[0].workspaces[0]
    ids = {s.session.session_id for s in row.sessions}
    provenances = {s.session.provenance for s in row.sessions}
    assert ids == {minted, hand}
    assert provenances == {"grove_launched", "fs_discovered"}
    assert row.sessions[0].session.session_id == minted  # minted materialized → primary


# ─── dirty_files (uncommitted churn streams before any commit) ───────────────


def test_dirty_files_change_emits_delta(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """An uncommitted file in the worktree changes the fingerprint and streams
    a delta — the agent-is-editing signal must not wait for a commit."""
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="dirty"))

    deltas: list[DashboardDelta] = []
    service.subscribe(deltas.append)
    service.poll_once()  # primes fingerprints
    deltas.clear()

    (Path(state.worktree_path) / "scratch.txt").write_text("wip\n", encoding="utf-8")
    service.poll_once()

    rows = [d for d in deltas if d.kind == "session_activity"]
    assert len(rows) == 1
    assert rows[0].workspace is not None
    assert rows[0].workspace.dirty_files == 1


def test_dirty_files_best_effort_zero_when_worktree_gone(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    service, registry = env
    repo = _init_repo(tmp_path / "repo")
    state = registry.get(repo).create(CreateWorkspaceRequest(agent_name="claude", title="gone"))
    registry.get(repo).pause(state.id)  # removes the worktree dir

    row = service.snapshot().projects[0].workspaces[0]
    assert row.dirty_files == 0


def test_snapshot_includes_config_declared_empty_project(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A config-declared repo with zero workspaces surfaces as a ProjectGroup, so
    the webapp new-workspace dialog (which reads `/activity`) can target it."""
    empty_repo = _init_repo(tmp_path / "empty")
    cfg = GroveConfig.model_validate({"projects": [str(empty_repo)]})
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    service = ActivityService(registry=RepoRegistry(cfg=cfg, store=store))

    snap = service.snapshot()
    by_root = {g.repo_root: g for g in snap.projects}
    assert str(empty_repo) in by_root
    assert by_root[str(empty_repo)].workspaces == ()


def test_snapshot_groups_nested_projects_distinctly(fake_tmux: FakeTmux, tmp_path: Path) -> None:
    """Two declared subdirs of one repo each surface as a distinct ProjectGroup,
    with each workspace attributed to its cwd group while sharing the repo."""
    repo = _init_repo(tmp_path / "mono")
    homelab = repo / "homelab"
    homelab.mkdir()
    (homelab / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "homelab", "--no-verify"], cwd=repo, check=True, capture_output=True
    )

    cfg = GroveConfig.model_validate(
        {"tmux": {"session_prefix": "test-"}, "projects": [str(repo), str(homelab)]}
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    service = ActivityService(registry=registry)
    mgr = registry.get(repo)
    mgr.create(CreateWorkspaceRequest(agent_name="shell", title="root-task"))
    mgr.create(CreateWorkspaceRequest(agent_name="shell", title="nested-task", project_cwd=homelab))

    snap = service.snapshot()

    by_cwd = {g.cwd: g for g in snap.projects}
    assert set(by_cwd) == {str(repo.resolve()), str(homelab.resolve())}
    # Both anchor at the same repo root; the workspaces split by cwd.
    assert all(g.repo_root == str(repo.resolve()) for g in snap.projects)
    assert [w.state.title for w in by_cwd[str(repo.resolve())].workspaces] == ["root-task"]
    assert [w.state.title for w in by_cwd[str(homelab.resolve())].workspaces] == ["nested-task"]


# ─── config-dir asymmetry (transcript_context) ──────────────────────────────
#
# The user-visible symptom, end to end, not just the writer or the read scope
# in isolation: an agent pinned to a non-default CLAUDE_CONFIG_DIR (the
# hermetic-profile mechanism) writes its transcript there, while the READING
# process — the daemon, the TUI, this very test's own `sessions_for` call —
# has a *different* ambient CLAUDE_CONFIG_DIR. An unscoped `sessions_for`
# glob-scans the reader's own dir, finds nothing, and the workspace's card
# pins at STARTING forever with a blank agent axis. `create` records where
# the agent actually wrote (`TranscriptContext.for_launch`) and
# `sessions_for` scopes its read to match (`_transcript_scope`).


def _write_transcript(config_dir: Path, sid: str, cwd: str) -> Path:
    """A real-shaped Claude transcript under ``config_dir/projects/<encoded
    cwd>``, ending on an assistant ``end_turn`` so the parsed state is a real
    one (WAITING) once the transcript is actually found — STARTING is what a
    MISSING transcript parses to, so ending mid-turn would confound the two."""
    folder = config_dir / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h1","timestamp":"2026-07-25T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"do the thing"}}}}\n'
        f'{{"type":"assistant","uuid":"a1","requestId":"r1",'
        f'"timestamp":"2026-07-25T08:00:05.000Z","isSidechain":false,"cwd":"{cwd}",'
        f'"message":{{"id":"m1","role":"assistant","stop_reason":"end_turn",'
        f'"content":[{{"type":"text","text":"done"}}]}}}}\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def pinned_asymmetry(
    fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ActivityService, WorkspaceManager, Path]:
    """A workspace whose agent pins ``CLAUDE_CONFIG_DIR`` at a dir the READING
    process's own ambient env does not name. `pinned_dir` is where the agent
    (and this fixture, standing in for it) writes; `reader_dir`/``Path.home``
    is what an unscoped adapter read would consult instead."""
    del fake_tmux
    pinned_dir = tmp_path / "profiles" / "work"
    reader_dir = tmp_path / "reader-home" / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(reader_dir))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "reader-home")

    cfg = GroveConfig.model_validate(
        {
            "tmux": {"session_prefix": "test-"},
            "agents": [
                {
                    "name": "work",
                    "command": "claude",
                    "kind": "claude_code",
                    "env": {"CLAUDE_CONFIG_DIR": str(pinned_dir)},
                }
            ],
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)
    return ActivityService(registry=registry), mgr, pinned_dir


def test_pinned_workspace_blends_to_a_real_state(
    pinned_asymmetry: tuple[ActivityService, WorkspaceManager, Path],
) -> None:
    """The symptom, in user terms: a workspace whose agent writes its
    transcript under a pinned config dir resolves its session instead of
    showing STARTING forever with a blank agent axis."""
    service, mgr, pinned_dir = pinned_asymmetry
    state = mgr.create(CreateWorkspaceRequest(agent_name="work", title="pinned"))
    sid = state.agent_session_id
    assert sid is not None

    # The agent, running under the PINNED env, writes its transcript there.
    written = _write_transcript(pinned_dir, sid, str(state.agent_cwd))
    # The reader's ambient dir holds nothing — this IS the whole asymmetry.
    assert os.environ["CLAUDE_CONFIG_DIR"] != str(pinned_dir)

    sessions = service.sessions_for(mgr, mgr.get(state.id))

    assert len(sessions) == 1
    primary = sessions[0]
    assert primary.session.transcript_path == written
    assert primary.activity.state is not AgentActivityState.STARTING
    assert primary.activity.human_turns == 1


def test_without_the_context_it_still_pins_at_starting(
    pinned_asymmetry: tuple[ActivityService, WorkspaceManager, Path],
) -> None:
    """The BEFORE arm, reproduced on the SAME code and SAME on-disk transcript
    by clearing the recorded context: clearing it collapses the scope to a
    bare ``nullcontext``, ``transcript_scan_cwds`` to ``scan_cwds``, and
    ``minted_cwd`` to ``agent_cwd`` — reproducing the exact unscoped-read
    failure rather than standing in for it. Keep this arm: without it, a
    future refactor could silently regress the override while every other
    test in this file (and `test_transcript_context.py`) stays green, because
    they all prove the writer records a context — none of them proves the
    bug those records exist to close is actually gone."""
    service, mgr, pinned_dir = pinned_asymmetry
    state = mgr.create(CreateWorkspaceRequest(agent_name="work", title="pinned"))
    sid = state.agent_session_id
    assert sid is not None
    _write_transcript(pinned_dir, sid, str(state.agent_cwd))

    mgr.store.save(replace(state, transcript_context=None))
    sessions = service.sessions_for(mgr, mgr.get(state.id))

    assert len(sessions) == 1
    assert sessions[0].session.transcript_path is None
    assert sessions[0].activity.state is AgentActivityState.STARTING


def test_unpinned_workspace_is_unaffected(
    fake_tmux: FakeTmux, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: the overwhelmingly common case (no pin, no override) still
    resolves via the ambient env exactly as it always has — the override
    path only ADDS a scoped read, it never changes the unpinned one."""
    del fake_tmux
    home = tmp_path / "home"
    ambient = home / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(ambient))
    monkeypatch.setattr(Path, "home", lambda: home)
    cfg = GroveConfig.model_validate({"tmux": {"session_prefix": "test-"}})
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    registry = RepoRegistry(cfg=cfg, store=store)
    service = ActivityService(registry=registry)
    repo = _init_repo(tmp_path / "repo")
    mgr = registry.get(repo)

    state = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="plain"))
    assert state.transcript_context is None
    sid = state.agent_session_id
    assert sid is not None
    written = _write_transcript(ambient, sid, str(state.agent_cwd))

    sessions = service.sessions_for(mgr, mgr.get(state.id))

    assert sessions[0].session.transcript_path == written
    assert sessions[0].activity.state is not AgentActivityState.STARTING


def test_scope_does_not_leak_into_the_readers_env(
    pinned_asymmetry: tuple[ActivityService, WorkspaceManager, Path],
) -> None:
    """The control that guards a real hazard, not just a nicety: the scope
    mutates PROCESS-GLOBAL env while held, and a leak would corrupt every
    subsequent read the daemon makes, not just this one. Checked both
    directions: the var is restored to its exact prior value, and that prior
    value is never the pinned dir it was scoped to during the call."""
    service, mgr, pinned_dir = pinned_asymmetry
    state = mgr.create(CreateWorkspaceRequest(agent_name="work", title="pinned"))
    assert state.agent_session_id is not None
    _write_transcript(pinned_dir, state.agent_session_id, str(state.agent_cwd))
    before = os.environ["CLAUDE_CONFIG_DIR"]

    service.sessions_for(mgr, mgr.get(state.id))

    assert os.environ["CLAUDE_CONFIG_DIR"] == before
    assert before != str(pinned_dir)


# ─── the dead-agent signal ──────────────────────────────────────────────────


def _exit_record(workspace_id: str, code: int) -> None:
    """Write what the agent pane's shell writes when the command exits."""
    path = core_paths.agent_exit_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{code}\n", encoding="utf-8")


def test_a_dead_agent_reads_as_error_with_a_reason(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """The agent process is not the pane.

    A dead agent leaves a live fallback shell and no transcript, which blends to
    STARTING and settles to IDLE — indistinguishable from an agent that is
    simply quiet, which can leave a container workspace sitting dead behind a
    `grove create` that had already exited 0. The recorded exit is the one
    signal that is a fact rather than an inference.
    """
    service, registry = env
    mgr = registry.get(_init_repo(tmp_path / "repo"))
    created = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="dead"))
    _exit_record(created.id, 7)

    state = next(s for s in mgr.list() if s.id == created.id)
    primary = service.sessions_for(mgr, state)[0]

    assert primary.activity.state is AgentActivityState.ERROR
    # The reason, not just the state: acting on this must not require capturing
    # a pane by hand, which was the only way to find the original incident.
    assert primary.activity.current_task == "agent exited with status 7"


def test_a_slow_starting_agent_is_never_reported_as_failed(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """The anti-flake rule, and it holds by SHAPE rather than by a threshold.

    No record means the command has not exited, so a slow start is
    indistinguishable from a healthy run — there is no window in which this
    signal can misfire, and `_settle`'s existing hysteresis is untouched.
    """
    service, registry = env
    mgr = registry.get(_init_repo(tmp_path / "repo"))
    created = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="slow"))

    state = next(s for s in mgr.list() if s.id == created.id)
    primary = service.sessions_for(mgr, state)[0]

    assert primary.activity.state is AgentActivityState.STARTING
    assert primary.activity.current_task is None


def test_an_agent_the_user_quit_cleanly_is_not_an_error(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path
) -> None:
    """A zero exit is a person closing their agent, not a failure."""
    service, registry = env
    mgr = registry.get(_init_repo(tmp_path / "repo"))
    created = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="quit"))
    _exit_record(created.id, 0)

    state = next(s for s in mgr.list() if s.id == created.id)
    primary = service.sessions_for(mgr, state)[0]

    assert primary.activity.state is not AgentActivityState.ERROR


def test_the_recorded_exit_outranks_a_stale_sidecar_push(
    env: tuple[ActivityService, RepoRegistry], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering against the hook push, which is the subtle one.

    An agent that pushed `SessionStart` and then died would read as working
    forever off that stale push — the sidecar supersedes the poll by design. The
    recorded exit is newer information than any push, so it is applied after the
    override rather than before it.
    """
    service, registry = env
    sidecar_dir = tmp_path / "sidecars"
    sidecar_dir.mkdir()
    monkeypatch.setattr(core_paths, "agent_sidecar_dir", lambda: sidecar_dir)
    mgr = registry.get(_init_repo(tmp_path / "repo"))
    created = mgr.create(CreateWorkspaceRequest(agent_name="claude", title="pushed-then-died"))
    assert created.agent_session_id is not None
    (sidecar_dir / f"{created.agent_session_id}.json").write_text(
        json.dumps(
            {
                "session_id": created.agent_session_id,
                "state": AgentActivityState.WORKING.value,
                "ts": datetime.now(UTC).isoformat(),
                "event": "SessionStart",
                "cwd": str(created.agent_cwd),
            }
        ),
        encoding="utf-8",
    )
    _exit_record(created.id, 1)

    state = next(s for s in mgr.list() if s.id == created.id)
    primary = service.sessions_for(mgr, state)[0]

    assert primary.activity.state is AgentActivityState.ERROR


# ─── one bad repo must not black out the fleet ──────────────────────────────


def test_a_repo_with_unparseable_config_degrades_alone(
    fake_tmux: FakeTmux, tmp_state_dir: Path, tmp_path: Path
) -> None:
    """A stray comma in ONE repo's config used to blank the dashboard for every repo.

    `snapshot`/`poll_once` resolve each repo's cascade through
    `RepoRegistry.get`, which raises `ConfigError` on invalid JSON — and nothing
    caught it, though three per-workspace reads in the same loop already carry
    the opposite convention. The assertion that matters is NOT that no exception
    escapes: it is that the healthy repo's WORKSPACE ROWS still arrive, since
    collecting them and then discarding them is exactly what happened.
    """
    del fake_tmux, tmp_state_dir
    healthy = _init_repo(tmp_path / "healthy")
    broken = _init_repo(tmp_path / "broken")
    (broken / ".grove").mkdir()
    (broken / ".grove" / "config.json").write_text('{"worktree": {},}\n', encoding="utf-8")
    cfg = GroveConfig.model_validate(
        {
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            "projects": [str(healthy), str(broken)],
        }
    )
    registry = RepoRegistry(
        cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json"), config_loader=load_config
    )
    service = ActivityService(registry=registry)
    registry.get(healthy).create(CreateWorkspaceRequest(agent_name="claude", title="alive"))

    snap = service.snapshot()

    by_name = {g.repo_name: g for g in snap.projects}
    assert set(by_name) == {"healthy", "broken"}
    # The whole point: real rows, not merely a non-empty group list.
    assert [w.state.title for w in by_name["healthy"].workspaces] == ["alive"]
    assert by_name["healthy"].error is None
    assert snap.total_workspaces == 1
    # And the broken repo is NAMED rather than silently missing — a vanished
    # project reads as a healthy fleet, which is the same bug one size smaller.
    degraded = by_name["broken"]
    assert degraded.workspaces == ()
    assert degraded.error is not None
    assert "ConfigError" in degraded.error

    # The poll path shares the failure and must survive it too; the healthy
    # repo's workspace still produces a delta.
    events: list[object] = []
    service.subscribe(events.append)
    service.poll_once()
    assert events


def test_a_repo_that_becomes_readable_is_picked_up_without_a_restart(
    fake_tmux: FakeTmux, tmp_state_dir: Path, tmp_path: Path
) -> None:
    """The unreadable repo is never marked bridged, so recovery needs no restart.

    `_ensure_bridged` runs FIRST in both entry points, so an unguarded raise
    there is what actually took the dashboard down — the guards further in were
    never reached. Skipping without recording the repo as bridged is what makes
    fixing the config enough.
    """
    del fake_tmux, tmp_state_dir
    broken = _init_repo(tmp_path / "broken")
    (broken / ".grove").mkdir()
    (broken / ".grove" / "config.json").write_text("{,}\n", encoding="utf-8")
    cfg = GroveConfig.model_validate(
        {
            "tmux": {"session_prefix": "test-"},
            "hooks": {"enabled": False},
            "projects": [str(broken)],
        }
    )
    registry = RepoRegistry(
        cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json"), config_loader=load_config
    )
    service = ActivityService(registry=registry)
    assert service.snapshot().projects[0].error is not None

    (broken / ".grove" / "config.json").write_text("{}\n", encoding="utf-8")

    healed = service.snapshot()
    assert healed.projects[0].error is None
