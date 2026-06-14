"""``grove show`` — the read-only single-workspace inspector (issue #51).

In-process via CliRunner against a real tmp git repo and the FakeTmux seam —
no daemon, no real tmux. ``show`` composes the engine's best-effort read seams
(``WorkspaceManager.peek`` + ``SessionExplorer``); it never mutates, so these
tests exercise the create → show happy path, cwd inference, the clean
unknown-workspace error, and the renderer in isolation.

The cwd-inference resolver (:func:`_resolve_or_infer_workspace`) and the
renderer (:class:`WorkspaceInspection`) are the real logic and are unit-tested
directly (no CliRunner) — the same split ``BranchFlags`` follows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import GroveError, SessionListing, WorkspacePeek, WorkspaceState
from grove.core.agents import (
    AgentActivity,
    AgentActivityState,
    DigestEntry,
    SessionSummary,
    SessionTurn,
)
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    Placement,
    WorkspaceStatus,
)
from grove.tui.cli import app
from grove.tui.cli_workspace import WorkspaceInspection, _resolve_or_infer_workspace
from tests.conftest import FakeTmux


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Path:
    """cwd inside a real repo, Grove state sandboxed, tmux/git side effects faked."""
    del tmp_state_dir, fake_tmux  # used via monkeypatch
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner, title: str = "inspect me") -> str:
    """Create a workspace via the CLI and return its id."""
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    return created.output.splitlines()[0].split("created ", 1)[1].strip()


def _worktree_of(runner: CliRunner, ws_id: str) -> Path:
    """The worktree path of a workspace, read off `grove ls` (always JSON)."""
    listed = runner.invoke(app, ["ls"])
    assert listed.exit_code == 0, listed.output
    rows = json.loads(listed.output)
    match = next(r for r in rows if r["id"] == ws_id)
    return Path(match["worktree_path"])


# ─── grove show <ref> ────────────────────────────────────────────────────────


def test_show_by_prefix_renders_sections(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner, "fix login")

    result = runner.invoke(app, ["show", ws_id[:8]])
    assert result.exit_code == 0, result.output
    out = result.output
    # Identity: id + agent + branch are present.
    assert ws_id in out
    assert "claude" in out
    assert "fix-login" in out  # the auto-slugged branch
    # Each perspective renders its header.
    assert "git" in out
    assert "agent" in out
    assert "transcript" in out


def test_show_no_ref_infers_from_cwd(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no argument, `show` infers the workspace from the worktree the cwd
    sits inside — resolving the same workspace `show <id>` would."""
    del project
    ws_id = _create(runner, "infer me")
    worktree = _worktree_of(runner, ws_id)

    monkeypatch.chdir(worktree)
    result = runner.invoke(app, ["show"])
    assert result.exit_code == 0, result.output
    assert ws_id in result.output


def test_show_unknown_workspace_is_clean_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["show", "deadbeef"])
    assert result.exit_code == 1
    assert "no workspace matches" in result.output
    assert "Traceback" not in result.output


# ─── _resolve_or_infer_workspace (pure, no CliRunner) ────────────────────────


def _state(ws_id: str, worktree: Path) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=ws_id,
        title=ws_id,
        repo_root=str(worktree.parent),
        branch=f"grove/{ws_id}",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


class _FakeManager:
    """Duck-typed manager exposing only `.list()` — the resolver's sole seam."""

    def __init__(self, states: list[WorkspaceState]) -> None:
        self._states = states

    def list(self) -> list[WorkspaceState]:
        return self._states


def test_infer_prefers_most_specific_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worktree nested under a ROOT workspace (the repo root) wins — the
    longest ancestor-or-equal match, not the first."""
    root = tmp_path / "repo"
    nested = root / ".worktrees" / "feature"
    nested.mkdir(parents=True)
    root_ws = _state("rootws", root)
    nested_ws = _state("nestedws", nested)
    manager = _FakeManager([root_ws, nested_ws])

    monkeypatch.chdir(nested)
    resolved = _resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]
    assert resolved.id == "nestedws"

    monkeypatch.chdir(root)
    resolved = _resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]
    assert resolved.id == "rootws"


def test_infer_outside_any_worktree_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _FakeManager([_state("ws", tmp_path / "elsewhere")])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(GroveError, match="run inside a workspace worktree"):
        _resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]


# ─── WorkspaceInspection renderer (pure, hand-built peek) ─────────────────────


def _peek(tmp_path: Path, *, snapshot: str | None) -> WorkspacePeek:
    now = datetime.now(UTC)
    return WorkspacePeek(
        state=WorkspaceState(
            id="abc123",
            title="render me",
            repo_root=str(tmp_path),
            branch="grove/render-me",
            base_branch="main",
            worktree_path=str(tmp_path / "wt"),
            tmux_session="grove-abc123",
            agent_name="claude",
            status=WorkspaceStatus.RUNNING,
            created_at=now,
            updated_at=now,
            branch_provenance=BranchProvenance.GROVE_CREATED,
            placement=Placement.WORKTREE,
        ),
        base_ahead=2,
        base_behind=1,
        diff_added=10,
        diff_removed=3,
        dirty_files=4,
        recent_commits=(CommitSummary(sha="deadbee0", subject="do a thing", committed_at=now),),
        agent_snapshot=snapshot,
        snapshot_taken_at=now if snapshot else None,
    )


def test_emit_renders_identity_git_and_empty_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no primary session, agent/transcript degrade to short notes; git
    and identity always render."""
    inspection = WorkspaceInspection(peek=_peek(tmp_path, snapshot=None), primary=None, turns=())
    inspection.emit()
    out = capsys.readouterr().out
    assert "abc123  render me" in out
    assert "grove/render-me" in out
    assert "ahead 2 · behind 1" in out
    assert "diff +10 -3 · dirty 4" in out
    assert "deadbee0  do a thing" in out
    assert "(no agent session)" in out
    assert "(no transcript)" in out
    assert "(no live pane)" in out


def test_emit_renders_transcript_tail_and_strips_ansi(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    activity = AgentActivity(
        state=AgentActivityState.WORKING,
        model="claude-x",
        human_turns=3,
        assistant_replies=5,
        tool_calls=7,
        current_task="running the tests",
    )
    summary = SessionSummary(
        session_id="sess-1",
        adapter_kind="claude_code",
        transcript_path=None,
        cwd=str(tmp_path),
        created_at=None,
        modified_at=None,
        size_bytes=0,
        activity=activity,
    )
    listing = SessionListing(summary=summary, provenance="grove_launched")
    turns = (
        SessionTurn(
            user_text="please run tests",
            entries=(
                DigestEntry(role="assistant", text="on it"),
                DigestEntry(role="tool", text="2 tool calls"),
            ),
        ),
    )
    # ANSI color escapes in the pane must be stripped from the printed tail.
    snapshot = "\x1b[31mred line\x1b[0m\nplain line"
    inspection = WorkspaceInspection(
        peek=_peek(tmp_path, snapshot=snapshot), primary=listing, turns=turns
    )
    inspection.emit()
    out = capsys.readouterr().out
    # Agent metrics line.
    assert "working" in out
    assert "claude-x" in out
    assert "3 turns · 5 replies" in out
    assert "running the tests" in out  # the live current-task line
    # Transcript glyphs (the cli_sessions style).
    assert "❯ please run tests" in out  # noqa: RUF001
    assert "⏺ on it" in out
    assert "⚒ 2 tool calls" in out
    # Pane tail rendered, ANSI stripped (no raw escape bytes leak through).
    assert "red line" in out
    assert "plain line" in out
    assert "\x1b[" not in out
