"""GET /workspaces/{id}/sessions + .../turns + /sessions (project AND host) + /sessions/{sid}/turns.

Modeled on test_activity_endpoints.py: TestClient over build_app with a
store-backed fake workspace, transcripts written into a sandboxed
CLAUDE_CONFIG_DIR keyed by the workspace's (nonexistent) worktree path —
transcripts outlive worktrees, so the endpoints must work without one.
The repo-root directory itself does exist (a plain dir, not a git repo):
the project scan shells ``git worktree list`` with the root as cwd, and
``worktree_paths`` falls back to ``[root]`` when that fails.

The host scope adds two real-I/O seams the project scope never touched, both
neutralized here rather than globally: the ``/proc`` walk (patched, so
liveness is asserted from synthetic runtimes instead of whatever happens to
run on the test machine) and the mewbo adapter's REST listing (patched, so no
test opens a socket — the adapter's own tests inject an httpx transport, which
is why this stays module-local and not an autouse conftest fixture).
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.process import LiveRuntime
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config

MINTED_SID = "33333333-3333-4333-8333-333333333333"
HAND_SID = "44444444-4444-4444-8444-444444444444"
ROOT_SID = "55555555-5555-4555-8555-555555555555"
FLEET_THREAD_ID = "77777777-7777-4777-8777-777777777777"


def _state(ws_id: str, repo_root: str, *, session_id: str | None = None) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"t-{ws_id}",
        repo_root=repo_root,
        branch=f"b-{ws_id}",
        base_branch="main",
        worktree_path=f"{repo_root}/.grove/worktrees/{ws_id}",
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,  # PAUSED → reconcile is trivial, no tmux
        created_at=now,
        updated_at=now,
        agent_session_id=session_id,
    )


def _write_transcript(
    claude_home: Path,
    sid: str,
    cwd: str,
    *,
    mtime: int,
    prompt: str,
    born_at: datetime | None = None,
) -> None:
    """``born_at`` is the session's birth (first-record timestamp), distinct
    from ``mtime`` — needed by a discovered (non-minted) listing to pass the
    created_at adoption gate (`WorkspaceState.adopts_session`)."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
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


def _write_fleet_child(
    claude_home: Path, parent_sid: str, cwd: str, thread_id: str, *, prompt: str
) -> None:
    """A fleet child's sidechain transcript at the real on-host layout —
    ``<encoded-cwd>/<parent-sid>/subagents/agent-<thread-id>.jsonl`` — nested
    under its PARENT top-level session's own dir, never a listing entry of
    its own, which is why a direct id lookup 404s."""
    sub_dir = (
        claude_home / "projects" / _ClaudeHome.encode_cwd(Path(cwd)) / parent_sid / "subagents"
    )
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / f"agent-{thread_id}.jsonl").write_text(
        f'{{"type":"user","uuid":"su1","isSidechain":true,"agentId":"{thread_id}",'
        f'"timestamp":"2026-06-09T08:00:01.000Z","message":{{"role":"user","content":"{prompt}"}}}}\n'
        f'{{"type":"assistant","uuid":"sa1","isSidechain":true,"agentId":"{thread_id}",'
        '"timestamp":"2026-06-09T08:00:02.000Z","message":{"id":"sm1","role":"assistant",'
        '"stop_reason":"end_turn","content":[{"type":"text","text":"Done."}]}}\n',
        encoding="utf-8",
    )


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


@pytest.fixture
def runtimes(monkeypatch: pytest.MonkeyPatch) -> list[LiveRuntime]:
    """The ``/proc`` scan the catalog folds for liveness, made deterministic.

    Returned mutable so a test appends the runtime it wants seen; empty means
    "nothing running", which is what every non-liveness test needs regardless
    of what the developer's machine happens to be running.
    """
    live: list[LiveRuntime] = []
    monkeypatch.setattr(
        "grove.core.process.list_agent_runtimes",
        lambda **_: tuple(live),
    )
    return live


@pytest.fixture(autouse=True)
def _offline_mewbo(monkeypatch: pytest.MonkeyPatch) -> None:
    """No socket: the mewbo adapter's scans are REST calls to a default
    ``127.0.0.1:5125``, and both the project scan (``list_sessions``) and the
    host scan (``discover_all``) reach for one."""
    monkeypatch.setattr("grove.core.agents.mewbo.MewboAdapter.list_sessions", lambda self, cwd: [])
    monkeypatch.setattr("grove.core.agents.mewbo.MewboAdapter.discover_all", lambda self: ())


@pytest.fixture
def client(
    tmp_state_dir: Path, claude_home: Path, runtimes: list[LiveRuntime]
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    repo_root = tmp_state_dir / "repo-a"
    repo_root.mkdir()  # the project scan needs a real cwd for `git worktree list`
    state = _state("a1", str(repo_root), session_id=MINTED_SID)
    store.save(state)
    _write_transcript(claude_home, MINTED_SID, state.worktree_path, mtime=2_000, prompt="minted")
    _write_transcript(
        claude_home,
        HAND_SID,
        state.worktree_path,
        mtime=3_000,
        prompt="by hand",
        # A discovered (fs_discovered) listing must be born at/after the
        # workspace's created_at to pass `WorkspaceState.adopts_session` —
        # the minted listing needs no such stamp, it is never gated.
        born_at=state.created_at + timedelta(seconds=1),
    )
    # Hand-staged at the repo root: no workspace owns that cwd → attribution None.
    _write_transcript(claude_home, ROOT_SID, str(repo_root), mtime=4_000, prompt="root staged")
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as test_client:
        yield test_client


# ─── GET /workspaces/{id}/sessions ──────────────────────────────────────────


def test_sessions_listing_newest_first_with_provenance(client: TestClient) -> None:
    resp = client.get("/workspaces/a1/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["session_id"] for s in body] == [HAND_SID, MINTED_SID]
    by_id = {s["session_id"]: s for s in body}
    assert by_id[MINTED_SID]["provenance"] == "grove_launched"
    assert by_id[HAND_SID]["provenance"] == "fs_discovered"
    assert by_id[MINTED_SID]["first_prompt"] == "minted"
    assert by_id[MINTED_SID]["workspace_id"] == "a1"
    # A project-scoped row is fully parsed, so it carries what a catalog row
    # cannot; only the transcript's own path stays host-private.
    assert by_id[MINTED_SID]["activity"] is not None
    assert by_id[MINTED_SID]["size_bytes"] > 0
    assert "transcript_path" not in by_id[MINTED_SID]


def test_sessions_limit(client: TestClient) -> None:
    body = client.get("/workspaces/a1/sessions", params={"limit": 1}).json()
    assert [s["session_id"] for s in body] == [HAND_SID]


def test_sessions_unknown_workspace_404(client: TestClient) -> None:
    resp = client.get("/workspaces/nope/sessions")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_sessions_candidates_flag_is_ungated(
    client: TestClient, claude_home: Path, tmp_state_dir: Path
) -> None:
    """`?candidates=true` flips to the ungated remap-picker scan: a
    session born before the workspace — the gate drops it from the default,
    attributed view — still appears so a UI can offer it to pin, while the
    scan stays scoped to the workspace's cwd (the repo-root staged session
    leaks into neither view)."""
    worktree = f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    stale_sid = "66666666-6666-4666-8666-666666666666"
    _write_transcript(
        claude_home,
        stale_sid,
        worktree,
        mtime=5_000,  # newest mtime → leads the ungated list
        prompt="pre-existing session",
        born_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    default_ids = [s["session_id"] for s in client.get("/workspaces/a1/sessions").json()]
    candidate_ids = [
        s["session_id"]
        for s in client.get("/workspaces/a1/sessions", params={"candidates": "true"}).json()
    ]

    assert stale_sid not in default_ids  # gated out of the attributed view
    assert stale_sid in candidate_ids  # kept for the picker
    assert candidate_ids[0] == stale_sid  # newest-first by mtime
    assert ROOT_SID not in candidate_ids  # still cwd-scoped, no repo-root leak


# ─── GET /workspaces/{id}/sessions/{sid}/turns ──────────────────────────────


def test_turns_returns_session_detail(client: TestClient) -> None:
    resp = client.get(f"/workspaces/a1/sessions/{MINTED_SID}/turns")
    assert resp.status_code == 200
    body = resp.json()
    assert body["session"]["session_id"] == MINTED_SID
    assert [t["user_text"] for t in body["turns"]] == ["minted"]


def test_turns_unknown_session_404_with_typed_envelope(client: TestClient) -> None:
    resp = client.get("/workspaces/a1/sessions/99999999-9999-4999-8999-999999999999/turns")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "agent_session_not_found"


def test_turns_falls_back_to_a_fleet_childs_own_thread(
    client: TestClient, claude_home: Path, tmp_state_dir: Path
) -> None:
    """A fleet row's ``session_id`` is the Claude sub-agent thread id — it
    never appears in the workspace's own session listing, so the primary
    listing match misses and the route falls back to
    ``SessionExplorer.subagent_turns`` before 404ing."""
    worktree = f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    _write_fleet_child(claude_home, MINTED_SID, worktree, FLEET_THREAD_ID, prompt="Explore the bug")

    resp = client.get(f"/workspaces/a1/sessions/{FLEET_THREAD_ID}/turns")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session"]["session_id"] == FLEET_THREAD_ID
    assert [t["user_text"] for t in body["turns"]] == ["Explore the bug"]


def test_turns_bogus_id_404s_even_with_a_fleet_child_present(
    client: TestClient, claude_home: Path, tmp_state_dir: Path
) -> None:
    """A real fleet child exists (so the fallback loop has something to scan
    through), but a bogus id that matches neither a listed session nor any
    fleet child still hits the unchanged typed 404 — the fallback must not
    swallow a genuine miss."""
    worktree = f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    _write_fleet_child(claude_home, MINTED_SID, worktree, FLEET_THREAD_ID, prompt="Explore the bug")

    resp = client.get("/workspaces/a1/sessions/99999999-9999-4999-8999-999999999999/turns")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "agent_session_not_found"


# ─── GET /sessions (project-scoped) ──────────────────────────────────────────


def test_project_sessions_spans_worktrees_with_attribution(
    client: TestClient, tmp_state_dir: Path
) -> None:
    resp = client.get("/sessions", params={"repo": str(tmp_state_dir / "repo-a")})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["session_id"] for s in body] == [ROOT_SID, HAND_SID, MINTED_SID]
    by_id = {s["session_id"]: s for s in body}
    minted = by_id[MINTED_SID]
    assert minted["provenance"] == "grove_launched"
    assert minted["workspace_id"] == "a1"
    assert minted["workspace_title"] == "t-a1"
    assert minted["workspace_branch"] == "b-a1"
    root = by_id[ROOT_SID]
    assert root["provenance"] == "fs_discovered"
    assert root["workspace_id"] is None
    assert root["workspace_title"] is None
    assert root["workspace_branch"] is None
    # The transcript's own path never crosses the wire; the directory the
    # session ran in deliberately does (Session Catalog) — see the view.
    assert "transcript_path" not in minted
    assert minted["cwd"] == f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"


def test_project_sessions_limit(client: TestClient, tmp_state_dir: Path) -> None:
    body = client.get(
        "/sessions", params={"repo": str(tmp_state_dir / "repo-a"), "limit": 1}
    ).json()
    assert [s["session_id"] for s in body] == [ROOT_SID]


def test_project_sessions_limit_bounds_422(client: TestClient, tmp_state_dir: Path) -> None:
    repo = str(tmp_state_dir / "repo-a")
    assert client.get("/sessions", params={"repo": repo, "limit": 0}).status_code == 422
    assert client.get("/sessions", params={"repo": repo, "limit": 201}).status_code == 422


def test_project_sessions_unknown_repo_404(client: TestClient, tmp_state_dir: Path) -> None:
    resp = client.get("/sessions", params={"repo": str(tmp_state_dir / "nope")})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_repo_root"


# ─── GET /sessions (host-scoped catalog) ─────────────────────────────────────


def test_host_sessions_span_every_repo_including_ones_grove_never_managed(
    client: TestClient, claude_home: Path, tmp_state_dir: Path
) -> None:
    """Omitting ``repo`` widens the SAME route to host scope: every session in
    the store, including one recorded in a repo no workspace has ever touched
    — the reason the catalog exists at all."""
    (tmp_state_dir / "repo-a" / ".git").mkdir()  # walk-up marker for the row's project
    other = tmp_state_dir / "elsewhere"
    (other / ".git").mkdir(parents=True)
    other_sid = "88888888-8888-4888-8888-888888888888"
    _write_transcript(claude_home, other_sid, str(other), mtime=6_000, prompt="unmanaged")

    resp = client.get("/sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["session_id"] for s in body] == [other_sid, ROOT_SID, HAND_SID, MINTED_SID]

    by_id = {s["session_id"]: s for s in body}
    minted = by_id[MINTED_SID]
    assert minted["provenance"] == "grove_launched"
    assert minted["primary"] is True
    assert minted["workspace_id"] == "a1"
    assert minted["workspace_title"] == "t-a1"
    assert minted["cwd"] == f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    assert minted["project"] == {
        "repo_root": str(tmp_state_dir / "repo-a"),
        "repo_name": "repo-a",
        "is_worktree": False,
        "is_grove_managed": True,
    }
    # A repo Grove has never managed still lists, honestly unattributed.
    unmanaged = by_id[other_sid]
    assert unmanaged["workspace_id"] is None
    assert unmanaged["provenance"] == "fs_discovered"
    assert unmanaged["project"]["repo_name"] == "elsewhere"
    assert unmanaged["project"]["is_grove_managed"] is False


def test_host_sessions_are_metadata_only_and_say_so_with_nulls(
    client: TestClient, tmp_state_dir: Path
) -> None:
    """The host scan never parses a transcript, so the parse-derived fields are
    null rather than zero — and the transcript path still never crosses."""
    row = next(s for s in client.get("/sessions").json() if s["session_id"] == MINTED_SID)
    assert row["activity"] is None
    assert row["size_bytes"] is None
    assert row["first_prompt"] is None
    assert row["workspace_branch"] is None  # the host scan annotates a workspace, not its branch
    assert row["git_branch"] == "main"  # ...but the SESSION's own branch is a free head read
    assert "transcript_path" not in row


def test_host_sessions_places_a_session_with_no_enclosing_repo(
    client: TestClient, claude_home: Path, tmp_path: Path
) -> None:
    """A cwd under no git repo yields ``project: null`` — the row is never
    dropped and no project is invented."""
    loose = tmp_path / "loose-dir"
    loose.mkdir()
    loose_sid = "99999999-9999-4999-8999-999999999999"
    _write_transcript(claude_home, loose_sid, str(loose), mtime=7_000, prompt="no repo")

    row = next(s for s in client.get("/sessions").json() if s["session_id"] == loose_sid)
    assert row["project"] is None
    assert row["cwd"] == str(loose)


def test_host_sessions_limit_is_newest_first_and_bounded(client: TestClient) -> None:
    assert [s["session_id"] for s in client.get("/sessions", params={"limit": 1}).json()] == [
        ROOT_SID
    ]
    assert client.get("/sessions", params={"limit": 0}).status_code == 422
    assert client.get("/sessions", params={"limit": 201}).status_code == 422


def test_host_sessions_live_flag_reflects_a_running_agent_in_that_cwd(
    client: TestClient, claude_home: Path, tmp_state_dir: Path, runtimes: list[LiveRuntime]
) -> None:
    """``live`` is the cwd-level join of a running same-kind process against a
    FRESH transcript — a stale transcript in a live directory stays false, which
    is what keeps the signal honest rather than "some agent is in this folder"."""
    worktree = f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    fresh_sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _write_transcript(
        claude_home, fresh_sid, worktree, mtime=int(time.time()), prompt="running now"
    )
    runtimes.append(
        LiveRuntime(pid=4242, kind="claude_code", cwd=Path(worktree), started_at=datetime.now(UTC))
    )

    by_id = {s["session_id"]: s for s in client.get("/sessions").json()}
    assert by_id[fresh_sid]["live"] is True
    assert by_id[MINTED_SID]["live"] is False  # same cwd, 1970 transcript → stale, not live


# ─── GET /sessions/{sid}/turns (the workspace-less drill-in) ─────────────────


def test_catalog_turns_render_a_session_that_belongs_to_no_workspace(
    client: TestClient, claude_home: Path, tmp_path: Path
) -> None:
    loose = tmp_path / "loose-dir"
    loose.mkdir()
    loose_sid = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _write_transcript(claude_home, loose_sid, str(loose), mtime=8_000, prompt="hello from nowhere")

    resp = client.get(
        f"/sessions/{loose_sid}/turns", params={"kind": "claude_code", "cwd": str(loose)}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session"]["session_id"] == loose_sid
    assert body["session"]["workspace_id"] is None
    assert body["session"]["cwd"] == str(loose)
    assert [t["user_text"] for t in body["turns"]] == ["hello from nowhere"]


def test_catalog_turns_serve_a_workspace_owned_session_too(
    client: TestClient, tmp_state_dir: Path
) -> None:
    """The route resolves by coordinates, not by absence of a workspace — a
    Grove-launched session reached this way keeps its attribution."""
    worktree = f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    resp = client.get(
        f"/sessions/{MINTED_SID}/turns", params={"kind": "claude_code", "cwd": worktree}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["session"]["workspace_id"] == "a1"


def test_catalog_turns_404_on_unknown_id_or_mismatched_coordinates(
    client: TestClient, tmp_state_dir: Path
) -> None:
    worktree = f"{tmp_state_dir / 'repo-a'}/.grove/worktrees/a1"
    unknown = client.get(
        "/sessions/cccccccc-cccc-4ccc-8ccc-cccccccccccc/turns",
        params={"kind": "claude_code", "cwd": worktree},
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["error"] == "agent_session_not_found"
    # Right id, wrong kind / wrong directory — both are a miss, never another
    # session's transcript.
    assert (
        client.get(
            f"/sessions/{MINTED_SID}/turns", params={"kind": "codex", "cwd": worktree}
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/sessions/{MINTED_SID}/turns",
            params={"kind": "claude_code", "cwd": str(tmp_state_dir / "repo-a")},
        ).status_code
        == 404
    )


def test_catalog_turns_requires_its_coordinates(client: TestClient) -> None:
    assert client.get(f"/sessions/{MINTED_SID}/turns").status_code == 422


# ─── auth + OpenAPI ─────────────────────────────────────────────────────────


def test_session_routes_require_auth(tmp_state_dir: Path) -> None:
    store = JsonWorkspaceStore()
    store.save(_state("a1", str(tmp_state_dir / "repo-a")))
    app = build_app(cfg=GroveConfig(), store=store)  # auth enabled (default)
    with TestClient(app) as authed:
        assert authed.get("/workspaces/a1/sessions").status_code == 401
        assert authed.get(f"/workspaces/a1/sessions/{MINTED_SID}/turns").status_code == 401
        assert authed.get("/sessions", params={"repo": "/x"}).status_code == 401
        assert authed.get("/sessions").status_code == 401  # host scope is not a back door
        assert (
            authed.get(
                f"/sessions/{MINTED_SID}/turns", params={"kind": "claude_code", "cwd": "/x"}
            ).status_code
            == 401
        )


def test_openapi_documents_session_routes_and_views(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert "/workspaces/{ws_id}/sessions" in spec["paths"]
    assert "/workspaces/{ws_id}/sessions/{session_id}/turns" in spec["paths"]
    assert "/sessions" in spec["paths"]
    assert "/sessions/{session_id}/turns" in spec["paths"]
    schemas = spec["components"]["schemas"]
    # These must appear so the webapp codegen picks the wire types up.
    assert "SessionSummaryView" in schemas
    assert "SessionDetailView" in schemas
    assert "SessionProjectView" in schemas
    # The attribution trio rides the existing view (additive, nullable).
    props = schemas["SessionSummaryView"]["properties"]
    assert "workspace_title" in props
    assert "workspace_branch" in props
    # Catalog scope is a VALUE of the existing repo param, not a sibling route.
    repo_param = next(
        p for p in spec["paths"]["/sessions"]["get"]["parameters"] if p["name"] == "repo"
    )
    assert repo_param["required"] is False
