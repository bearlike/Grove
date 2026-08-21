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
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.hook import ClaudeHook
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import GroveError, WorkspaceNotFound
from grove.core.manager import WorkspaceManager
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
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
