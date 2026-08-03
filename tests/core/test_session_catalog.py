"""Host-wide session enumeration (`SessionCatalog`).

Real tmp git repos + real worktrees (via the manager's create with FakeTmux),
sandboxed Claude config dir with hand-written realistic transcripts. Pins the
catalog's own invariants: repos are discovered FROM session cwds (never a
filesystem crawl), the `.git`-walk-up resolution, honest degradation for a
cwd-less or repo-less session, and provenance/workspace annotation unioned
across every known repo.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.agents.model import SessionRef
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.process import LiveRuntime
from grove.core.registry import RepoRegistry
from grove.core.sessions import CatalogEntry, SessionCatalog
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

MINTED_SID = "11111111-1111-4111-8111-111111111111"
ROOT_SID = "22222222-2222-4222-8222-222222222222"
UNMANAGED_SID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(autouse=True)
def _no_real_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file builds a catalog against `tmp_path` cwds that
    no real host process could ever share — but a real `/proc` scan running
    during the suite is nondeterministic noise this module doesn't want.
    Neutered by default; the dedicated liveness tests override it."""
    monkeypatch.setattr("grove.core.process.list_agent_runtimes", lambda: ())


def _cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
        }
    )


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "state.json"


@pytest.fixture
def manager(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path, state_path: Path
) -> WorkspaceManager:
    del fake_tmux
    store = JsonWorkspaceStore(path=state_path)
    return WorkspaceManager(repo_root=tmp_repo, cfg=_cfg(tmp_path), store=store)


@pytest.fixture
def registry(tmp_path: Path, state_path: Path) -> RepoRegistry:
    """A registry over the SAME on-disk store the ``manager`` fixture writes
    to — independent ``WorkspaceManager`` instances sharing one JSON store
    file, exactly like the daemon's real multi-repo topology."""
    store = JsonWorkspaceStore(path=state_path)
    return RepoRegistry(cfg=_cfg(tmp_path), store=store)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _write_transcript(
    claude_home: Path, sid: str, cwd: Path, *, mtime: int, branch: str = "main"
) -> Path:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h-{sid[:4]}","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"{branch}",'
        '"message":{"role":"user","content":"hi"}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def _write_cwdless_transcript(claude_home: Path, sid: str, *, mtime: int) -> Path:
    """A transcript whose head read never reveals a cwd (~2 % of real
    transcripts) — dropped into an arbitrary encoded folder name since no
    real cwd exists to encode."""
    folder = claude_home / "projects" / "unresolvable"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n{"type":"summary","summary":"no cwd"}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=10)


def test_scan_finds_managed_and_unmanaged_sessions_across_every_repo(
    manager: WorkspaceManager, registry: RepoRegistry, claude_home: Path, tmp_repo: Path
) -> None:
    """The catalog's core promise: sessions from a repo Grove never heard of
    (not in `cfg.projects`, no persisted workspace) still appear — repos are
    discovered FROM the session's own recorded cwd, never a host-wide `.git`
    crawl."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="widget work"))
    assert state.agent_session_id is not None
    _write_transcript(claude_home, state.agent_session_id, Path(state.worktree_path), mtime=3_000)
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=2_000)

    # An entirely separate repo Grove was never told about.
    unmanaged_repo = tmp_repo.parent / "unmanaged-repo"
    unmanaged_repo.mkdir()
    _git(unmanaged_repo, "init", "-b", "main")
    _write_transcript(claude_home, UNMANAGED_SID, unmanaged_repo, mtime=1_000)

    entries = SessionCatalog(registry).scan()
    by_id = {e.ref.session_id: e for e in entries}

    assert set(by_id) == {state.agent_session_id, ROOT_SID, UNMANAGED_SID}
    # Newest-first by transcript mtime.
    assert [e.ref.session_id for e in entries] == [
        state.agent_session_id,
        ROOT_SID,
        UNMANAGED_SID,
    ]

    minted_entry = by_id[state.agent_session_id]
    assert minted_entry.provenance == "grove_launched"
    assert minted_entry.workspace_id == state.id
    assert minted_entry.workspace_title == "widget work"
    assert minted_entry.project is not None
    assert minted_entry.project.is_grove_managed is True
    assert minted_entry.project.is_worktree is True  # a linked worktree's .git is a file
    assert minted_entry.ref.git_branch == "main"  # from the session, never re-derived from git

    root_entry = by_id[ROOT_SID]
    assert root_entry.provenance == "fs_discovered"
    assert root_entry.workspace_id is None  # repo root has no ROOT-placement workspace here
    assert root_entry.project is not None
    assert root_entry.project.is_grove_managed is True
    assert root_entry.project.is_worktree is False

    unmanaged_entry = by_id[UNMANAGED_SID]
    assert unmanaged_entry.provenance == "fs_discovered"
    assert unmanaged_entry.workspace_id is None
    assert unmanaged_entry.project is not None
    assert unmanaged_entry.project.repo_name == "unmanaged-repo"
    assert unmanaged_entry.project.is_grove_managed is False  # never heard of, never crawled for


def test_scan_degrades_a_cwdless_session_without_dropping_it(
    registry: RepoRegistry, claude_home: Path
) -> None:
    _write_cwdless_transcript(claude_home, UNMANAGED_SID, mtime=1_000)

    entries = SessionCatalog(registry).scan()

    assert len(entries) == 1
    assert entries[0].ref.session_id == UNMANAGED_SID
    assert entries[0].ref.cwd is None
    assert entries[0].project is None
    assert entries[0].provenance == "fs_discovered"


def test_scan_degrades_a_session_with_no_enclosing_repo_without_dropping_it(
    registry: RepoRegistry, claude_home: Path, tmp_path: Path
) -> None:
    bare_dir = tmp_path / "no-git-here"
    bare_dir.mkdir()
    _write_transcript(claude_home, UNMANAGED_SID, bare_dir, mtime=1_000)

    entries = SessionCatalog(registry).scan()

    assert len(entries) == 1
    assert entries[0].ref.cwd == str(bare_dir)
    assert entries[0].project is None  # honestly unresolvable, never fabricated, never dropped


def test_scan_limit_caps_after_sorting(
    registry: RepoRegistry, claude_home: Path, tmp_repo: Path
) -> None:
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000)
    _write_transcript(claude_home, MINTED_SID, tmp_repo, mtime=2_000)

    entries = SessionCatalog(registry).scan(limit=1)

    assert len(entries) == 1
    assert entries[0].ref.session_id == MINTED_SID  # the newer of the two


def test_scan_does_not_surface_a_repo_with_no_session_recorded_in_it(
    registry: RepoRegistry, claude_home: Path, tmp_path: Path
) -> None:
    """A sibling directory with a `.git` marker but no recorded session must
    never appear — the catalog discovers repos FROM sessions' own cwds,
    never by scanning the filesystem for `.git` directories."""
    other_repo = tmp_path / "never-touched-repo"
    other_repo.mkdir()
    (other_repo / ".git").mkdir()
    _write_transcript(claude_home, ROOT_SID, tmp_path / "the-only-cwd", mtime=1_000)

    entries = SessionCatalog(registry).scan()

    assert len(entries) == 1
    # `the-only-cwd` has no `.git` ancestor of its own — the sibling repo is
    # never picked up just because it exists on disk.
    assert entries[0].project is None
    assert all(e.project is None or e.project.repo_root != other_repo for e in entries)


# ─── fold_liveness (pure — zero I/O, no /proc, no subprocess) ──────────────

_STARTED_AT = datetime(2026, 6, 9, 8, 0, 0, tzinfo=UTC)


def _ref(
    sid: str, cwd: str | None, *, kind: str = "claude_code", mtime: float = 1_000.0
) -> SessionRef:
    return SessionRef(
        session_id=sid,
        adapter_kind=kind,
        cwd=cwd,
        transcript_path=None,
        birth=None,
        mtime=mtime,
        git_branch=None,
    )


def _entry(ref: SessionRef) -> CatalogEntry:
    return CatalogEntry(ref=ref, provenance="fs_discovered", project=None)


def test_fold_liveness_marks_live_on_matching_kind_and_fresh_cwd() -> None:
    entry = _entry(_ref("s1", "/work/one", mtime=1_000.0))
    runtimes = [
        LiveRuntime(pid=1, kind="claude_code", cwd=Path("/work/one"), started_at=_STARTED_AT)
    ]

    folded = SessionCatalog.fold_liveness([entry], runtimes, now=1_000.0 + 60)

    assert folded[0].live is True


def test_fold_liveness_ignores_a_kind_mismatch_at_the_same_cwd() -> None:
    entry = _entry(_ref("s1", "/work/one", kind="claude_code", mtime=1_000.0))
    runtimes = [LiveRuntime(pid=1, kind="codex", cwd=Path("/work/one"), started_at=_STARTED_AT)]

    folded = SessionCatalog.fold_liveness([entry], runtimes, now=1_000.0 + 60)

    assert folded[0].live is False


def test_fold_liveness_treats_a_stale_transcript_as_not_live() -> None:
    """A runtime at the right cwd/kind isn't enough on its own — the row's
    OWN transcript must also be fresh (within the sidecar staleness window),
    else a long-dead session sharing a cwd with today's new agent would
    misreport as live."""
    entry = _entry(_ref("s1", "/work/one", mtime=1_000.0))
    runtimes = [
        LiveRuntime(pid=1, kind="claude_code", cwd=Path("/work/one"), started_at=_STARTED_AT)
    ]

    folded = SessionCatalog.fold_liveness([entry], runtimes, now=1_000.0 + 10_000)

    assert folded[0].live is False


def test_fold_liveness_never_fabricates_a_1to1_binding_for_a_shared_cwd() -> None:
    """Two runtimes sharing one cwd (routine — 20+ concurrent `claude`
    processes were observed on one host) must fold to the SAME honest
    cwd-level signal for every session recorded there — never a made-up
    pick of which pid "owns" which session."""
    entry_a = _entry(_ref("s1", "/work/shared", mtime=1_000.0))
    entry_b = _entry(_ref("s2", "/work/shared", mtime=1_000.0))
    runtimes = [
        LiveRuntime(pid=1, kind="claude_code", cwd=Path("/work/shared"), started_at=_STARTED_AT),
        LiveRuntime(pid=2, kind="claude_code", cwd=Path("/work/shared"), started_at=_STARTED_AT),
    ]

    folded = SessionCatalog.fold_liveness([entry_a, entry_b], runtimes, now=1_000.0 + 60)

    assert folded[0].live is True
    assert folded[1].live is True
    # Neither CatalogEntry nor LiveRuntime carries a field that could ever
    # express "this pid is that session" — the type shape itself forbids it.
    assert not hasattr(folded[0], "pid")


def test_fold_liveness_treats_a_cwdless_ref_as_not_live() -> None:
    entry = _entry(_ref("s1", None, mtime=1_000.0))
    runtimes = [
        LiveRuntime(pid=1, kind="claude_code", cwd=Path("/anywhere"), started_at=_STARTED_AT)
    ]

    folded = SessionCatalog.fold_liveness([entry], runtimes, now=1_000.0 + 60)

    assert folded[0].live is False


def test_scan_marks_a_row_live_when_a_real_runtime_matches(
    registry: RepoRegistry,
    claude_home: Path,
    tmp_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end through `scan()`: a session whose cwd/kind matches a
    detected runtime, with a fresh transcript, renders `live=True`."""
    _write_transcript(claude_home, ROOT_SID, tmp_repo, mtime=1_000)
    monkeypatch.setattr(
        "grove.core.process.list_agent_runtimes",
        lambda: (LiveRuntime(pid=1, kind="claude_code", cwd=tmp_repo, started_at=_STARTED_AT),),
    )
    monkeypatch.setattr("time.time", lambda: 1_000.0 + 5)

    entries = SessionCatalog(registry).scan()

    assert len(entries) == 1
    assert entries[0].live is True
