"""Which session the derived todo axis reads.

The todo axis is PULL-only by decision — nothing pushes a list in, Grove folds
the agent's own ``TodoWrite``/``update_plan``/Task calls out of the transcript.
That makes "which session" the whole correctness question, and keying it on
``WorkspaceState.agent_session_id`` got it wrong for two SHIPPED populations,
both reproduced against real adapters before this file existed:

* **codex mints no id at all** (no launch flag exists, so `_mint_agent_session_id`
  returns ``None`` and the field stays empty for the workspace's whole life) —
  so every codex workspace answered 404 from ``GET /workspaces/{id}/todo``, an
  empty ``grove show`` section, an empty MCP tool and blank card counts, while
  ``CodexAdapter.latest_todo`` parsed its ``update_plan`` plan perfectly;
* a **rotated or ended claude id is a dead pointer** — the live conversation runs
  under another id in the same cwd, which the dashboard already promotes to
  primary, while the todo read stayed pinned to the dead mint and answered
  ``None``.

Two resolutions with the same OUTCOME and opposite cost profiles are pinned
here, because collapsing them is the tempting refactor that reintroduces a
daemon-CPU bug: the per-REQUEST id seam scans (`_todo_session_id`), the
~1 Hz poll seam never does — its caller hands the session over.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.activity import ActivityService
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.codex import CodexAdapter
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import AgentSessionNotFound
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.sessions import SessionExplorer
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux


def _iso(dt: datetime) -> str:
    """A transcript timestamp string. Births must postdate the workspace's own
    ``created_at`` (real wall-clock at test run) to clear the adoption gate —
    a hard-coded past date is filtered as stale history, which looks exactly
    like the bug this file pins."""
    return dt.isoformat().replace("+00:00", "Z")


def _soon() -> datetime:
    return datetime.now(UTC) + timedelta(minutes=1)


@pytest.fixture
def cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "todo/"},
            "tmux": {"session_prefix": "todo-"},
            "hooks": {"enabled": False},
        }
    )


@pytest.fixture
def store(tmp_path: Path) -> JsonWorkspaceStore:
    return JsonWorkspaceStore(path=tmp_path / "state.json")


@pytest.fixture
def manager(
    tmp_repo: Path, fake_tmux: FakeTmux, cfg: GroveConfig, store: JsonWorkspaceStore
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _write_claude_todo(claude_home: Path, cwd: Path, session_id: str, item: str) -> None:
    """A minimal but REAL claude transcript: one human turn, one ``TodoWrite``.

    Two lines rather than a fixture blob so the birth timestamp can be pushed
    past the workspace's ``created_at``; the block shapes are the ones pinned in
    ``agents/CLAUDE.md`` against on-host JSONL.
    """
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    born = _iso(_soon())
    (folder / f"{session_id}.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "user",
                        "uuid": "u1",
                        "timestamp": born,
                        "isSidechain": False,
                        "cwd": str(cwd),
                        "message": {"role": "user", "content": "go"},
                    }
                ),
                json.dumps(
                    {
                        "type": "assistant",
                        "uuid": "a1",
                        "requestId": "r1",
                        "isSidechain": False,
                        "cwd": str(cwd),
                        "timestamp": born,
                        "message": {
                            "id": "m1",
                            "role": "assistant",
                            "stop_reason": "tool_use",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "tw1",
                                    "name": "TodoWrite",
                                    "input": {
                                        "todos": [{"content": item, "status": "in_progress"}]
                                    },
                                }
                            ],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _write_codex_plan(codex_home: Path, cwd: Path, session_id: str) -> None:
    """A minimal but REAL codex rollout: ``session_meta`` (the only record
    carrying the cwd) plus an ``update_plan`` ``function_call`` whose
    ``arguments`` is a JSON STRING carrying ``plan[]`` — the shape pinned
    on-host in ``agents/CLAUDE.md``, not a guessed fixture."""
    folder = codex_home / "sessions" / "2099" / "04" / "28"
    folder.mkdir(parents=True, exist_ok=True)
    born = _iso(_soon())
    lines = [
        {
            "timestamp": born,
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(cwd), "model_provider": "openai"},
        },
        {
            "timestamp": born,
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "call_id": "c1",
                "name": "update_plan",
                "arguments": json.dumps(
                    {
                        "plan": [
                            {"step": "Review the orchestration code", "status": "completed"},
                            {"step": "Design the refactor", "status": "in_progress"},
                        ]
                    }
                ),
            },
        },
    ]
    (folder / f"rollout-2099-04-28T13-43-44-{session_id}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )


# ─── the id seam: resolution reaches past the mint ──────────────────────────


def test_codex_workspace_todo_is_reachable_without_a_minted_id(
    manager: WorkspaceManager, codex_home: Path
) -> None:
    """A grove-created codex workspace has ``agent_session_id is None`` for its
    whole life, so a mint-keyed read alone would raise ``AgentSessionNotFound``
    — a 404 on every surface for an entire provider whose adapter reads the
    plan fine. Resolution falls through to the adoption-gated discovery the
    agent axis has always used."""
    state = manager.create(CreateWorkspaceRequest(agent_name="codex", title="plan me"))
    assert state.agent_kind == "codex"
    assert state.agent_session_id is None  # the premise: codex mints nothing

    sid = "019dd888-33cc-7461-bd07-cccccccccccc"
    _write_codex_plan(codex_home, Path(state.agent_cwd), sid)
    # The adapter could always read it — only the engine's key was wrong.
    assert CodexAdapter().latest_todo(Path(state.agent_cwd), sid) is not None

    todo = manager.latest_todo(state.id)

    assert todo is not None
    assert [(i.content, i.status) for i in todo.items] == [
        ("Review the orchestration code", "completed"),
        ("Design the refactor", "in_progress"),
    ]


def test_dead_minted_pointer_yields_to_the_live_session(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """A minted id that never materializes (rotated by ``/clear``, hand-restarted,
    ended) is a dead pointer while the live conversation runs under another id in
    the same cwd. The dashboard already promotes that successor to primary; the
    todo read must not stay pinned to the dead mint and answer ``None``."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="clear me"))
    assert state.agent_session_id is not None  # minted, but nothing on disk for it
    live = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    _write_claude_todo(claude_home, Path(state.agent_cwd), live, "Live item")

    todo = manager.latest_todo(state.id)

    assert todo is not None
    assert [i.content for i in todo.items] == ["Live item"]


def test_a_live_mint_never_pays_for_a_discovery_scan(
    manager: WorkspaceManager, claude_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The common case — a claude workspace whose minted id materialized — must
    short-circuit on a ``locate_transcripts`` glob and never reach the bounded
    scan. Pinned by spying the scan itself: this is the hot arm, and a
    "simplification" that always scans is invisible in every other assertion
    here because the ANSWER would be identical."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="alive"))
    assert state.agent_session_id is not None
    _write_claude_todo(claude_home, Path(state.agent_cwd), state.agent_session_id, "Minted item")

    scans: list[str] = []
    real = SessionExplorer.for_workspace

    def spy(self: SessionExplorer, workspace_id: str) -> object:
        scans.append(workspace_id)
        return real(self, workspace_id)

    monkeypatch.setattr(SessionExplorer, "for_workspace", spy)

    todo = manager.latest_todo(state.id)

    assert todo is not None
    assert [i.content for i in todo.items] == ["Minted item"]
    assert scans == []


def test_still_raises_when_neither_mint_nor_discovery_names_a_session(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """The 404-vs-``None`` contract is unchanged: a workspace with no mint AND
    nothing discoverable is genuinely sessionless. Only that raises — "a session
    exists but no todo tool has been called yet" stays a real ``None``."""
    del claude_home  # sandboxes the scan; deliberately left empty
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="nothing"))
    manager.store.save(replace(state, agent_session_id=None))

    with pytest.raises(AgentSessionNotFound):
        manager.latest_todo(state.id)


def test_an_unmaterialized_mint_with_nothing_discovered_answers_none(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """The STARTING window: minted, transcript not written yet, nothing else in
    the cwd. Resolution keeps the mint rather than degrading to "sessionless",
    so the caller gets the honest ``None`` instead of a 404."""
    del claude_home
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="starting"))
    assert state.agent_session_id is not None

    assert manager.latest_todo(state.id) is None


def test_a_root_recorded_session_resolves_for_a_nested_workspace(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """A nested ``project_subpath`` workspace's session can be recorded at the
    WORKTREE ROOT — the second entry of ``transcript_scan_cwds``, while the
    read is handed the first.

    It resolves anyway, and knowing WHY is what stops the next reader adding a
    union loop to ``latest_todo_for``: both filesystem adapters locate a
    transcript by globbing the session ID and use ``cwd`` only to break a tie
    between files claiming it. Pinned because that is a property of the
    adapters, not of this method — if a future adapter ever resolves strictly
    by cwd, this test is what fails."""
    sub = "pkg"
    (Path(manager.repo_root) / sub).mkdir()
    state = manager.create(
        CreateWorkspaceRequest(
            agent_name="claude", title="nested", project_cwd=str(Path(manager.repo_root) / sub)
        )
    )
    assert state.project_subpath == sub
    cwds = [Path(c) for c in state.transcript_scan_cwds]
    assert len(cwds) == 2, cwds  # agent_cwd first, worktree root second
    assert state.agent_session_id is not None
    # Recorded under the SECOND entry only.
    _write_claude_todo(claude_home, cwds[1], state.agent_session_id, "Root-recorded item")

    todo = manager.latest_todo(state.id)

    assert todo is not None
    assert [i.content for i in todo.items] == ["Root-recorded item"]


# ─── the poll seam: cheap, and handed its session ───────────────────────────


def test_latest_todo_for_does_no_discovery_of_its_own(
    manager: WorkspaceManager, codex_home: Path
) -> None:
    """The ~1 Hz seam must stay a read, never a scan: a discovery scan per
    workspace per tick is a daemon-CPU bug. With no mint and no handed-over
    session it answers ``None`` — and reaches for no explorer."""
    state = manager.create(CreateWorkspaceRequest(agent_name="codex", title="poll me"))
    sid = "019dd888-33cc-7461-bd07-dddddddddddd"
    _write_codex_plan(codex_home, Path(state.agent_cwd), sid)

    assert manager.latest_todo_for(state) is None
    assert manager.latest_todo_for(state, session_id=sid) is not None


def test_the_activity_tick_hands_over_the_session_it_adopted(
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    cfg: GroveConfig,
    store: JsonWorkspaceStore,
    codex_home: Path,
) -> None:
    """End to end through the real poll: a codex workspace's card carries todo
    COUNTS because ``sessions_for`` resolved the primary on the same tick and
    ``_workspace_activity`` passes it down — not because the poll went looking."""
    del fake_tmux  # used via monkeypatch
    registry = RepoRegistry(cfg=cfg, store=store)
    mgr = registry.get(tmp_repo)
    state = mgr.create(CreateWorkspaceRequest(agent_name="codex", title="carded"))
    _write_codex_plan(codex_home, Path(state.agent_cwd), "019dd888-33cc-7461-bd07-eeeeeeeeeeee")

    snapshot = ActivityService(registry=registry).snapshot()

    rows = [w for group in snapshot.projects for w in group.workspaces]
    (row,) = [w for w in rows if w.state.id == state.id]
    assert row.todo is not None, "codex workspace card carried no todo progress"
    assert (row.todo.total, row.todo.completed, row.todo.in_progress) == (2, 1, 1)
