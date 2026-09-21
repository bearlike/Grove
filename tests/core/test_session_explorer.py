"""Cross-worktree session aggregation (`SessionExplorer`).

Real tmp git repo + real worktrees (via the manager's create with FakeTmux),
sandboxed Claude config dir with hand-written realistic transcripts. Pins the
aggregation invariants: every worktree scanned, workspace + provenance
annotation, filters, and unique-prefix resolution.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents import SessionRef, claude_code, transcript_cache
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.manager import WorkspaceManager
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from grove.core.turn_count import TurnCountCache
from grove.core.workspace import Placement
from tests.conftest import FakeTmux

ROOT_SID = "11111111-1111-4111-8111-111111111111"
TREE_SID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def manager(tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path) -> WorkspaceManager:
    del fake_tmux
    cfg = GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _write_transcript(
    claude_home: Path,
    sid: str,
    cwd: Path,
    *,
    mtime: int,
    prompt: str,
    born_at: datetime | None = None,
) -> Path:
    """``born_at`` is the session's birth (first-record timestamp) — distinct
    from ``mtime``, the file's last-touched time. Most callers don't care and
    take the fixed default; a test exercising the created_at adoption gate
    (`WorkspaceState.adopts_session`) passes one relative to the workspace's
    own ``created_at``."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    born = born_at.isoformat().replace("+00:00", "Z") if born_at else "2026-06-09T08:00:00.000Z"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h-{sid[:4]}","timestamp":"{born}",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        f'"message":{{"role":"user","content":"{prompt}"}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def _write_codex_rollout(codex_home: Path, sid: str, cwd: Path, *, mtime: int, prompt: str) -> Path:
    """A minimal real-shaped codex rollout under the date-partitioned sessions
    tree: a ``session_meta`` head carrying the id + cwd, then one real human
    turn (a ``response_item`` user message, no preamble markers)."""
    folder = codex_home / "sessions" / "2026" / "06" / "09"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2026-06-09T08-00-00-{sid}.jsonl"
    path.write_text(
        '{"timestamp":"2026-06-09T08:00:00.000Z","type":"session_meta",'
        f'"payload":{{"id":"{sid}","timestamp":"2026-06-09T08:00:00.000Z","cwd":"{cwd}"}}}}\n'
        '{"timestamp":"2026-06-09T08:00:01.000Z","type":"response_item",'
        f'"payload":{{"type":"message","role":"user",'
        f'"content":[{{"type":"input_text","text":"{prompt}"}}]}}}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def test_lists_sessions_across_root_and_worktrees(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="widget work"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    # The Grove-minted session in the worktree, and a hand-started one at the root.
    _write_transcript(
        claude_home, state.agent_session_id, worktree, mtime=2_000, prompt="fix the widget"
    )
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="root question")

    listings = SessionExplorer(manager).list()

    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id, ROOT_SID]
    minted, hand = listings
    assert minted.provenance == "grove_launched"
    assert minted.workspace_id == state.id
    assert minted.workspace_title == "widget work"
    assert hand.provenance == "fs_discovered"
    assert hand.workspace_id is None  # repo root has no ROOT-placement workspace


def test_list_does_not_annotate_foreign_kind_sessions_to_a_root_workspace(
    manager: WorkspaceManager,
    claude_home: Path,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The project browse (`list`) annotates a session to a workspace by
    cwd-equality — but a ROOT workspace's cwd is the shared repo root, where a
    foreign-kind (codex) rollout can already live. That session is NOT the
    workspace's own (its adapter can't read it, `remap_session` rejects a kind
    mismatch), so it must render UNMAPPED (`workspace_id`/title None) rather than
    borrow the workspace's identity — while a same-kind session at the same cwd
    still annotates. `list` stays all-kinds (the codex row still appears); only
    the attribution is kind-gated."""
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    codex_sid = "019dd5d5-60fb-7461-bd07-b6e8cf342726"
    _write_codex_rollout(codex_home, codex_sid, tmp_repo, mtime=9_000, prompt="foreign codex work")

    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="root claude", branch_plan=RootBranch())
    )
    assert state.placement is Placement.ROOT
    assert state.agent_session_id is not None
    _write_transcript(claude_home, state.agent_session_id, tmp_repo, mtime=2_000, prompt="mine")

    by_id = {ls.summary.session_id: ls for ls in SessionExplorer(manager).list()}

    # The same-kind claude session IS annotated to the workspace.
    assert by_id[state.agent_session_id].workspace_id == state.id
    assert by_id[state.agent_session_id].workspace_title == "root claude"
    # The foreign-kind codex session still appears (all-kinds browse) but borrows
    # NO workspace identity — honest, unmapped history rather than mis-attribution.
    assert codex_sid in by_id
    assert by_id[codex_sid].workspace_id is None
    assert by_id[codex_sid].workspace_title is None
    assert by_id[codex_sid].workspace_branch is None
    assert by_id[codex_sid].provenance == "fs_discovered"


def test_paused_workspace_sessions_survive_worktree_removal(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """Transcripts outlive worktrees: a paused workspace's dir is gone from
    `git worktree list`, but its persisted path still gets scanned."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="pausable"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    _write_transcript(
        claude_home, state.agent_session_id, worktree, mtime=1_500, prompt="paused work"
    )
    manager.pause(state.id)
    assert not worktree.exists()

    listings = SessionExplorer(manager).list()
    assert [ls.summary.session_id for ls in listings] == [state.agent_session_id]
    assert listings[0].workspace_title == "pausable"


def test_filters_and_limit(manager: WorkspaceManager, claude_home: Path, tmp_repo: Path) -> None:
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="filterable"))
    assert state.agent_session_id is not None
    _write_transcript(
        claude_home,
        state.agent_session_id,
        Path(state.worktree_path),
        mtime=2_000,
        prompt="in the worktree",
    )
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="at the root")
    explorer = SessionExplorer(manager)

    assert len(explorer.list(agent="claude_code")) == 2
    assert explorer.list(agent="codex") == []
    by_title = explorer.list(workspace="filter")
    assert [ls.summary.session_id for ls in by_title] == [state.agent_session_id]
    assert len(explorer.list(limit=1)) == 1


def test_resolve_prefix_and_ambiguity(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="one")
    _write_transcript(claude_home, TREE_SID, tmp_repo, mtime=2_000, prompt="two")
    explorer = SessionExplorer(manager)

    assert explorer.resolve("1111").summary.session_id == ROOT_SID
    with pytest.raises(GroveError, match="ambiguous"):
        explorer.resolve("")  # empty prefix matches both
    with pytest.raises(GroveError, match="no session"):
        explorer.resolve("dead-beef")


def test_turns_read_through_the_adapter(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="hello there")
    turns = SessionExplorer(manager).turns("1111", last=5)
    assert len(turns) == 1
    assert turns[0].user_text == "hello there"


def test_recollect_reads_full_spine_and_keeps_only_direct_user_queries(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """Recollection delegates classification to the adapter's real-turn filter.

    The records deliberately take the on-disk shapes the provider writes rather
    than constructing `SessionQuery` objects: the regression is the ordinary
    `type:"user"` machinery that would otherwise impersonate user intent.
    """
    long_query = "x" * 5_001
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(tmp_repo)
    folder.mkdir(parents=True)

    def user(index: int, text: str, **extra: object) -> dict[str, object]:
        return {
            "type": "user",
            "uuid": f"u-{index}",
            "timestamp": f"2026-08-18T00:00:{index:02d}Z",
            "isSidechain": False,
            "cwd": str(tmp_repo),
            "message": {"role": "user", "content": text},
            **extra,
        }

    records = [
        user(1, "first direct instruction"),
        user(2, "/compact"),  # A direct slash command remains an instruction.
        user(
            3,
            "tool output",
            message={
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}],
            },
        ),
        user(4, "meta machinery", isMeta=True),
        user(5, "<command-name>review</command-name>"),
        user(6, "<bash-input>git status</bash-input>"),
        user(7, "Caveat: resumed session"),
        user(8, "replacement summary", isCompactSummary=True),
        user(9, "<task-notification><summary>done</summary></task-notification>"),
        user(10, '<teammate-message teammate_id="peer">done</teammate-message>'),
        {
            "type": "attachment",
            "timestamp": "2026-08-18T00:00:11Z",
            "attachment": {
                "type": "queued_command",
                "commandMode": "prompt",
                "origin": {"kind": "human"},
                "prompt": "message sent while the agent was busy",
            },
        },
        user(12, long_query),
    ]
    (folder / f"{ROOT_SID}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    queries = SessionExplorer(manager).recollect(ROOT_SID)

    assert [query.ordinal for query in queries] == [1, 2, 3, 4]
    assert [query.text for query in queries[:-1]] == [
        "first direct instruction",
        "/compact",
        "message sent while the agent was busy",
    ]
    assert queries[-1].text == long_query  # recollection never truncates a query
    assert queries[2].sent_at is not None  # queued prompt preserves both clocks


def test_recollect_excludes_codex_injected_preamble(
    manager: WorkspaceManager,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tmp_repo: Path,
) -> None:
    """Codex's real-turn filter, not a second recollection-only preamble rule."""
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    sid = "019dd5d5-60fb-7461-bd07-b6e8cf342726"
    folder = codex_home / "sessions" / "2026" / "08" / "18"
    folder.mkdir(parents=True)
    records = [
        {
            "timestamp": "2026-08-18T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": sid, "cwd": str(tmp_repo)},
        },
        {
            "timestamp": "2026-08-18T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<environment_context>host</environment_context>\n# AGENTS.md",
                    }
                ],
            },
        },
        {
            "timestamp": "2026-08-18T00:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "make the change"}],
            },
        },
    ]
    (folder / f"rollout-2026-08-18T00-00-00-{sid}.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )

    assert [query.text for query in SessionExplorer(manager).recollect(sid)] == ["make the change"]


def test_for_workspace_scopes_to_one_directory(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """`for_workspace` is the bounded per-request scan: only the workspace's own
    cwd, newest-first, provenance by minted-id equality — a root-level session
    must not leak in. A discovered session also has to pass the created_at
    adoption gate: born-after-create leads by mtime as before; a stale
    transcript born before the workspace existed (a leftover from whatever
    used to occupy this cwd) is filtered out even though it has the newest
    mtime of all — the exact shape of the stale-cwd adoption bug."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="scoped"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    after_create = state.created_at + timedelta(seconds=1)
    _write_transcript(
        claude_home,
        state.agent_session_id,
        worktree,
        mtime=2_000,
        prompt="mine",
        born_at=after_create,
    )
    _write_transcript(
        claude_home,
        TREE_SID,
        worktree,
        mtime=3_000,
        prompt="hand-started here",
        born_at=after_create,
    )
    stale_sid = "44444444-4444-4444-8444-444444444444"
    _write_transcript(
        claude_home,
        stale_sid,
        worktree,
        mtime=5_000,  # newest mtime of all — would wrongly lead without the gate
        prompt="stale leftover",
        born_at=state.created_at - timedelta(days=1),
    )
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=4_000, prompt="root noise")

    listings = SessionExplorer(manager).for_workspace(state.id)

    ids = [ls.summary.session_id for ls in listings]
    assert ids == [TREE_SID, state.agent_session_id]
    assert stale_sid not in ids
    by_id = {ls.summary.session_id: ls for ls in listings}
    assert by_id[state.agent_session_id].provenance == "grove_launched"
    assert by_id[TREE_SID].provenance == "fs_discovered"
    assert all(ls.workspace_id == state.id for ls in listings)


def test_for_workspace_root_placement_ignores_stale_repo_root_transcript(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """The bug report's exact shape: a ROOT-placement workspace's cwd IS the
    repo root, which may already hold transcripts from whatever previously
    lived there. A fresh ROOT workspace must not present that stale, older
    session as its own — even though it is the only (and thus "newest")
    session in the cwd."""
    stale_sid = "77777777-7777-4777-8777-777777777777"
    _write_transcript(
        claude_home,
        stale_sid,
        tmp_repo,
        mtime=1_000,
        prompt="a much older session",
        born_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="fresh root", branch_plan=RootBranch())
    )
    assert state.placement is Placement.ROOT
    assert Path(state.worktree_path).resolve() == tmp_repo.resolve()

    listings = SessionExplorer(manager).for_workspace(state.id)

    assert stale_sid not in [ls.summary.session_id for ls in listings]


def test_for_workspace_adopts_resumed_session_via_sidecar(
    manager: WorkspaceManager,
    claude_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`for_workspace` mirrors the ActivityService adoption rule: a
    session born BEFORE the workspace but proven live in the workspace's OWN pane
    by a post-create hook sidecar is kept (the resumed-in-pane case). The pane is
    verified against the minted session's sidecar (the reference pane), so birth
    alone — which filters it as stale — is not the only signal."""
    sidecar_dir = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: sidecar_dir)
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="scoped-resume"))
    worktree = Path(state.worktree_path)
    minted = state.agent_session_id
    assert minted is not None
    # The minted session's sidecar pins the workspace's reference pane %3.
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": minted, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%3",
        now=state.created_at + timedelta(seconds=1),
    )
    resumed = "55554444-3333-4222-8111-000099998888"
    _write_transcript(
        claude_home,
        resumed,
        worktree,
        mtime=5_000,
        prompt="resumed here",
        born_at=state.created_at - timedelta(hours=1),
    )
    # The resumed session is live in the SAME pane %3 after creation.
    ClaudeHook.record_event(
        {"hook_event_name": "SessionStart", "session_id": resumed, "cwd": str(worktree)},
        sidecar_dir=sidecar_dir,
        tmux_pane="%3",
        now=state.created_at + timedelta(seconds=4),
    )

    listings = SessionExplorer(manager).for_workspace(state.id)
    assert resumed in [ls.summary.session_id for ls in listings]


def test_for_workspace_scans_nested_agent_cwd(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """`for_workspace` scans agent_cwd (worktree/subpath), where a nested
    project's agent records its transcript — not the worktree root."""
    sub = tmp_repo / "services" / "api"
    sub.mkdir(parents=True)
    (sub / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "api", "--no-verify"], cwd=tmp_repo, check=True, capture_output=True
    )

    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="nested", project_cwd=sub)
    )
    assert state.project_subpath == "services/api"
    assert state.agent_session_id is not None
    _write_transcript(
        claude_home,
        state.agent_session_id,
        state.agent_cwd,
        mtime=2_000,
        prompt="nested session",
        born_at=state.created_at + timedelta(seconds=1),
    )

    listings = SessionExplorer(manager).for_workspace(state.id)
    assert state.agent_session_id in [ls.summary.session_id for ls in listings]


def test_for_workspace_scans_worktree_root_for_nested_project(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """A session hand-started at the WORKTREE ROOT of a nested project (cwd =
    worktree_path, not agent_cwd) is still discovered — `for_workspace` scans the
    UNION of {agent_cwd, worktree_path}. Scanning agent_cwd alone would drop it,
    leaving a repo-root `claude` untracked."""
    sub = tmp_repo / "services" / "api"
    sub.mkdir(parents=True)
    (sub / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "api", "--no-verify"], cwd=tmp_repo, check=True, capture_output=True
    )

    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="nested-root", project_cwd=sub)
    )
    worktree = Path(state.worktree_path)
    assert worktree != state.agent_cwd  # nested: the two scan cwds genuinely differ
    root_session = "abcd0000-0000-4000-8000-000000000000"
    _write_transcript(
        claude_home,
        root_session,
        worktree,  # recorded at the worktree ROOT, not the nested agent_cwd
        mtime=9_000,
        prompt="root session",
        born_at=state.created_at + timedelta(seconds=1),
    )

    listings = SessionExplorer(manager).for_workspace(state.id)
    assert root_session in [ls.summary.session_id for ls in listings]


def test_candidates_for_is_ungated_and_cwd_scoped(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """`candidates_for` is the remap-picker seam: the ungated sibling of
    `for_workspace`. It keeps a session the adoption gate drops — one born
    before the workspace (a dead-minted-pointer's live successor / foreign
    resumed session) — so a human can pin it, while staying scoped to the
    workspace's own cwd (a root-level session must not leak in)."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="pickable"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    _write_transcript(
        claude_home,
        state.agent_session_id,
        worktree,
        mtime=2_000,
        prompt="mine",
        born_at=state.created_at + timedelta(seconds=1),
    )
    # Born a full day BEFORE the workspace — the exact shape the gate rejects.
    stale_sid = "44444444-4444-4444-8444-444444444444"
    _write_transcript(
        claude_home,
        stale_sid,
        worktree,
        mtime=5_000,  # newest mtime → leads the ungated list
        prompt="pre-existing live session",
        born_at=state.created_at - timedelta(days=1),
    )
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=4_000, prompt="root noise")
    explorer = SessionExplorer(manager)

    gated = [ls.summary.session_id for ls in explorer.for_workspace(state.id)]
    ungated = [ls.summary.session_id for ls in explorer.candidates_for(state.id)]

    # The gate drops the pre-birth session; the picker keeps it.
    assert stale_sid not in gated
    assert stale_sid in ungated
    # Ungated is newest-first by mtime and still cwd-scoped (no root leak).
    assert ungated == [stale_sid, state.agent_session_id]
    by_id = {ls.summary.session_id: ls for ls in explorer.candidates_for(state.id)}
    assert by_id[state.agent_session_id].provenance == "grove_launched"
    assert by_id[stale_sid].provenance == "fs_discovered"
    assert all(ls.workspace_id == state.id for ls in explorer.candidates_for(state.id))


def test_scan_workspace_excludes_foreign_kind_sessions(
    manager: WorkspaceManager,
    claude_home: Path,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A claude_code ROOT workspace's cwd is the shared repo root, where the
    human also runs *other* tools — so a foreign-kind (codex) rollout can already
    live there. That session can never be this workspace's own: its adapter can't
    read it and `remap_session` rejects a kind mismatch. So neither the gated
    attribution (`for_workspace`) nor the ungated remap picker (`candidates_for`)
    may offer it — the picker must never surface a session the pin would reject —
    even though the all-kinds project browse (`list`) still finds it."""
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    codex_sid = "019dd5d5-60fb-7461-bd07-b6e8cf342726"
    _write_codex_rollout(codex_home, codex_sid, tmp_repo, mtime=9_000, prompt="foreign codex work")

    state = manager.create(
        CreateWorkspaceRequest(agent_name="claude", title="root claude", branch_plan=RootBranch())
    )
    assert state.placement is Placement.ROOT
    assert state.agent_session_id is not None
    # The workspace's own claude session in the same (root) cwd.
    _write_transcript(
        claude_home,
        state.agent_session_id,
        tmp_repo,
        mtime=2_000,
        prompt="mine",
        born_at=state.created_at + timedelta(seconds=1),
    )
    explorer = SessionExplorer(manager)

    # The codex rollout IS discoverable by the all-kinds project browse ...
    assert codex_sid in [ls.summary.session_id for ls in explorer.list(agent="codex")]
    # ... but both per-workspace scans exclude it by kind, keeping only the
    # workspace's own claude_code session.
    gated = [ls.summary.session_id for ls in explorer.for_workspace(state.id)]
    ungated = [ls.summary.session_id for ls in explorer.candidates_for(state.id)]
    assert codex_sid not in gated
    assert codex_sid not in ungated
    assert gated == [state.agent_session_id]
    assert ungated == [state.agent_session_id]
    assert all(ls.summary.adapter_kind == "claude_code" for ls in explorer.candidates_for(state.id))


def test_candidates_for_unknown_id_raises(manager: WorkspaceManager) -> None:
    with pytest.raises(WorkspaceNotFound):
        SessionExplorer(manager).candidates_for("deadbeef")


def test_for_workspace_unknown_id_raises(manager: WorkspaceManager) -> None:
    with pytest.raises(WorkspaceNotFound):
        SessionExplorer(manager).for_workspace("deadbeef")


def test_turns_for_matches_turns(
    manager: WorkspaceManager, claude_home: Path, tmp_repo: Path
) -> None:
    """`turns(ref)` is `turns_for(resolve(ref))` — the split must not drift."""
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000, prompt="hello there")
    explorer = SessionExplorer(manager)
    listing = explorer.resolve("1111")
    assert explorer.turns_for(listing, last=5) == explorer.turns("1111", last=5)


def _relocate_transcript(claude_home: Path, path: Path, folder_cwd: Path) -> Path:
    """Move an existing transcript into the folder Claude Code would encode for
    ``folder_cwd``, preserving its content and mtime — the on-disk shape of a
    session that entered a native ``.claude/worktrees/`` checkout mid-run.

    Measured on the reference host: Claude Code re-homes the whole transcript
    under the worktree it entered, while every record's own ``cwd`` keeps
    naming the directory that record was written in. So the folder encodes a
    directory the session may never have recorded, and no cwd the workspace
    knows encodes back to it.
    """
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(folder_cwd)
    folder.mkdir(parents=True, exist_ok=True)
    moved = folder / path.name
    stat = path.stat()
    moved.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    os.utime(moved, (stat.st_mtime, stat.st_mtime))
    return moved


def test_for_workspace_finds_minted_session_after_transcript_relocation(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """A minted session whose transcript Claude Code re-homed under a native
    worktree folder still lists — the relocation must not make it unreadable.

    This is the `/turns` 404 reproduced at the engine seam: the cwd-encoded
    folder scan cannot see the moved file, so the workspace listed NO sessions
    at all while the dashboard kept advertising the minted id off the store
    record. Resolution by identity (the UUID glob) is what recovers it.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="relocated"))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    written = _write_transcript(
        claude_home,
        state.agent_session_id,
        worktree,
        mtime=2_000,
        prompt="born at the worktree root",
        born_at=state.created_at + timedelta(seconds=1),
    )
    # The session enters `.claude/worktrees/<name>/` and Claude Code re-homes
    # the transcript there. Nothing in `transcript_scan_cwds` encodes to it.
    moved = _relocate_transcript(claude_home, written, worktree / ".claude" / "worktrees" / "feat")
    assert moved.is_file()
    assert not written.exists()

    listings = SessionExplorer(manager).for_workspace(state.id)

    ids = [ls.summary.session_id for ls in listings]
    assert ids == [state.agent_session_id]
    listing = listings[0]
    assert listing.provenance == "grove_launched"
    assert listing.workspace_id == state.id
    # The row must be a real parse of the moved file, not a synthesized stub:
    # its content is what the turns route serves.
    assert listing.summary.transcript_path == moved
    assert SessionExplorer(manager).turns_for(listing)


# ─── the metadata-only cost guarantee (#805) ─────────────────────────────────


@pytest.fixture
def count_parsers(monkeypatch: pytest.MonkeyPatch) -> Callable[[], int]:
    """Count WHOLE-TRANSCRIPT reads — the read-count seam.

    Asserting by COUNT rather than by elapsed time is the point: this host is
    contended, so a timing assertion measures the neighbours' load rather than
    this code.

    It counts TWO events, and the second is why: ``_TranscriptParser`` is the
    obvious one, but ``read_messages`` (which is how the deleted per-row
    ``duration`` column was computed) never constructs one — it projects the
    spine straight off ``TranscriptCache``. A fixture watching only the parser
    was VACUOUS against exactly that regression: restoring the second full parse
    per row left all five guards green. ``TranscriptCache._consume`` is the seam
    every whole-file read flows through, head reads included in neither.
    """
    calls = 0
    original_parser = claude_code._TranscriptParser.__init__
    original_consume = transcript_cache.TranscriptCache._consume

    def counting_init(self: object, *args: object, **kwargs: object) -> None:
        nonlocal calls
        calls += 1
        original_parser(self, *args, **kwargs)  # type: ignore[arg-type]

    def counting_consume(self: object, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original_consume(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(claude_code._TranscriptParser, "__init__", counting_init)
    monkeypatch.setattr(transcript_cache.TranscriptCache, "_consume", counting_consume)
    return lambda: calls


def _workspace_with_neighbours(
    manager: WorkspaceManager, claude_home: Path, *, neighbours: int, title: str = "crowded"
) -> tuple[str, str]:
    """A workspace whose own cwd already holds ``neighbours`` foreign transcripts.

    The shape the issue measured: a shared cwd where the listing's cost tracked
    the number of strangers in the directory rather than the session asked for.
    ``title`` is a parameter because a branch name derives from it at
    one-second resolution, so two workspaces made in the same test collide.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title=title))
    assert state.agent_session_id is not None
    worktree = Path(state.worktree_path)
    born = state.created_at + timedelta(seconds=1)
    _write_transcript(
        claude_home, state.agent_session_id, worktree, mtime=2_000, prompt="mine", born_at=born
    )
    for index in range(neighbours):
        _write_transcript(
            claude_home,
            f"{index:08d}-1111-4111-8111-{title[:4]:x<4}11111111",
            worktree,
            mtime=3_000 + index,
            prompt=f"neighbour {index}",
            born_at=born,
        )
    return (state.id, state.agent_session_id)


def test_for_workspace_parses_no_transcript_at_all(
    manager: WorkspaceManager, claude_home: Path, count_parsers: Callable[[], int]
) -> None:
    """The listing is built from bounded head reads — ZERO full parses.

    Before this guard the scan constructed one parser per transcript sharing the
    directory (plus a second, on a different fold key, for the duration column),
    so opening one workspace cost every neighbour's whole file. The row still
    carries everything a head read can answer.
    """
    workspace_id, minted = _workspace_with_neighbours(manager, claude_home, neighbours=6)

    listings = SessionExplorer(manager).for_workspace(workspace_id)

    assert count_parsers() == 0
    assert len(listings) == 7
    row = next(ls for ls in listings if ls.summary.session_id == minted)
    # Head-readable facts survive; parse products are honestly absent.
    assert row.summary.cwd == manager.get(workspace_id).worktree_path
    assert row.summary.git_branch == "main"
    assert row.summary.created_at is not None
    assert row.summary.first_prompt == "mine"
    assert row.summary.activity is None
    assert row.summary.title is None
    assert row.summary.last_prompt is None


def test_listing_cost_does_not_grow_with_the_number_of_neighbours(
    manager: WorkspaceManager, claude_home: Path, count_parsers: Callable[[], int]
) -> None:
    """O(neighbours) was the defect, so the guard is stated over two fleet sizes.

    A count that stays flat as the directory fills is the property; a single-size
    assertion could pass on a scan that merely got cheaper per file.
    """
    small, _ = _workspace_with_neighbours(manager, claude_home, neighbours=2, title="small")
    baseline = count_parsers()
    assert len(SessionExplorer(manager).for_workspace(small)) == 3
    after_small = count_parsers() - baseline

    large, _ = _workspace_with_neighbours(manager, claude_home, neighbours=12, title="large")
    baseline = count_parsers()
    assert len(SessionExplorer(manager).for_workspace(large)) == 13
    after_large = count_parsers() - baseline

    assert (after_small, after_large) == (0, 0)


def test_candidates_for_is_metadata_only_too(
    manager: WorkspaceManager, claude_home: Path, count_parsers: Callable[[], int]
) -> None:
    """The ungated remap-picker scan shares the gated scan's one derivation, so
    it must share its cost model — it is the surface a human opens to CHOOSE,
    where a multi-second stall is most visible."""
    workspace_id, _ = _workspace_with_neighbours(manager, claude_home, neighbours=5)

    listings = SessionExplorer(manager).candidates_for(workspace_id)

    assert count_parsers() == 0
    assert len(listings) == 6


def test_project_wide_list_is_metadata_only_until_enrichment_is_asked_for(
    manager: WorkspaceManager, claude_home: Path, count_parsers: Callable[[], int]
) -> None:
    """`list` is the browse scan and stays free; `enrich=True` is the opt-in the
    `grove sessions` table pays for, and it is bounded by DISPLAYED rows.

    Both halves matter: a scan that never enriched would silently drop the CLI's
    STATE/TURNS columns, and an enrichment applied before the limit would put the
    O(neighbours) cost straight back.
    """
    _workspace_with_neighbours(manager, claude_home, neighbours=5)
    explorer = SessionExplorer(manager)

    plain = explorer.list()
    assert count_parsers() == 0
    assert len(plain) == 6  # every transcript listed, none of them read
    assert all(ls.summary.activity is None for ls in plain)

    # Caches are cleared between the two measurements so this compares COLD
    # against COLD — otherwise the second call re-reads nothing it already read
    # and the scaling claim measures the memo instead of the scan.
    ClaudeCodeAdapter.clear_caches()
    baseline = count_parsers()
    one = explorer.list(limit=1, enrich=True)
    per_row = count_parsers() - baseline

    ClaudeCodeAdapter.clear_caches()
    baseline = count_parsers()
    three = explorer.list(limit=3, enrich=True)
    # Cost tracks DISPLAYED rows, never the six transcripts on disk — the whole
    # defect restated: the scan used to pay per neighbour. Stated as a ratio so
    # it pins the SCALING, not one identity-keyed read's own cost.
    assert per_row > 0
    assert count_parsers() - baseline == 3 * per_row

    assert (len(one), len(three)) == (1, 3)
    assert all(ls.summary.activity is not None for ls in three)
    top = three[0].summary.activity
    assert top is not None and top.human_turns == 1


def test_for_workspace_limit_bounds_the_rows_it_returns(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """`limit` reaches the scan rather than being applied to its output, and the
    bounded newest-first selection must agree with slicing the full sort."""
    workspace_id, _ = _workspace_with_neighbours(manager, claude_home, neighbours=6)
    explorer = SessionExplorer(manager)

    everything = explorer.for_workspace(workspace_id)
    bounded = explorer.for_workspace(workspace_id, limit=3)

    assert len(bounded) == 3
    assert [ls.summary.session_id for ls in bounded] == [
        ls.summary.session_id for ls in everything[:3]
    ]


def test_duration_comes_from_the_durable_cache_not_a_second_parse(
    manager: WorkspaceManager, claude_home: Path, tmp_path: Path, count_parsers: Callable[[], int]
) -> None:
    """`duration` was a SECOND full parse per row, on a different fold key.

    It now reads the same durable parse-fact cache the host catalog fills off the
    request path. A row the cache has not reached renders `None` — "not measured
    yet", never "no work" — and resolves to a number once a background pass has
    run, with no parse on the read path either way.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="timed"))
    assert state.agent_session_id is not None
    born = state.created_at + timedelta(seconds=1)
    path = _write_transcript(
        claude_home,
        state.agent_session_id,
        Path(state.worktree_path),
        mtime=2_000,
        prompt="do the work",
        born_at=born,
    )
    # The reply lands two minutes after the prompt, so the derived active span is
    # a real number. Both stamps ride the SAME clock as the birth: mixing a fixed
    # date with the workspace's own `created_at` produces a months-long span.
    replied = (born + timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            '{"type":"assistant","uuid":"a1","requestId":"r1","isSidechain":false,'
            f'"cwd":"{state.worktree_path}","timestamp":"{replied}",'
            '"message":{"id":"m1","role":"assistant",'
            '"model":"claude-opus-5","stop_reason":"end_turn",'
            '"usage":{"input_tokens":5,"output_tokens":2},'
            '"content":[{"type":"text","text":"done"}]}}\n'
        )
    os.utime(path, (2_000, 2_000))
    cache = TurnCountCache(path=tmp_path / "turns.json")
    explorer = SessionExplorer(manager, turn_counts=cache)

    cold = explorer.for_workspace(state.id)[0]
    assert count_parsers() == 0
    assert cold.duration is None
    assert cold.turn_count is None

    # The background pass pays the one parse, off the request path.
    cache.fill(
        [
            SessionRef(
                session_id=state.agent_session_id,
                adapter_kind="claude_code",
                cwd=state.worktree_path,
                transcript_path=path,
                birth=None,
                mtime=2_000.0,
            )
        ]
    )
    baseline = count_parsers()
    warm = explorer.for_workspace(state.id)[0]

    assert count_parsers() - baseline == 0
    assert warm.duration is not None
    assert warm.duration.active_ms == 2 * 60_000
    assert warm.turn_count == 1
