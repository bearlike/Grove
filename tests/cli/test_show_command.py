"""``grove show`` — the read-only single-workspace inspector.

In-process via CliRunner against a real tmp git repo and the FakeTmux seam —
no daemon, no real tmux. ``show`` composes the engine's best-effort read seams
(``WorkspaceManager.peek`` + ``SessionExplorer``); it never mutates, so these
tests exercise the create → show happy path, cwd inference, the clean
unknown-workspace error, and the renderer in isolation.

The cwd-inference resolver (:func:`resolve_or_infer_workspace`) and the
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
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.views import WorkspaceStateView
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    Placement,
    Runtime,
    WorkspaceStatus,
)
from grove.tui.cli import app
from grove.tui.cli_workspace import (
    WorkspaceInspection,
    _emit_runtime_marks,
    resolve_or_infer_workspace,
)
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
    # Scanned, not indexed at line 0: the engine may log before the result (an
    # unavailable container runtime falls back to the host loudly).
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


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


# ─── resolve_or_infer_workspace (pure, no CliRunner) ────────────────────────


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
    resolved = resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]
    assert resolved.id == "nestedws"

    monkeypatch.chdir(root)
    resolved = resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]
    assert resolved.id == "rootws"


def test_infer_outside_any_worktree_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _FakeManager([_state("ws", tmp_path / "elsewhere")])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(GroveError, match="run inside a workspace worktree"):
        resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]


def test_infer_refuses_an_equal_depth_tie_and_names_the_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two ROOT workspaces share the repo root, so the depths TIE — and a bare
    `depth > best_depth` would silently resolve to whichever `list()` yielded
    first, leaving `grove show`/`phase`/`tickets` each acting on a workspace
    the caller never named. The assertion is deliberately on the CANDIDATES,
    not just the raise: an error the user cannot act on would only move the
    problem.
    """
    root = tmp_path / "repo"
    root.mkdir()
    manager = _FakeManager([_state("rootone", root), _state("roottwo", root)])
    monkeypatch.chdir(root)

    with pytest.raises(GroveError) as excinfo:
        resolve_or_infer_workspace(manager, None)  # type: ignore[arg-type]

    message = str(excinfo.value)
    assert "2 workspaces share this directory" in message
    assert "rootone" in message
    assert "roottwo" in message


def test_infer_still_breaks_a_tie_by_specificity_before_refusing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collecting ties must not turn the ordinary nesting case into a refusal:
    a deeper worktree still wins outright over the root workspace above it."""
    root = tmp_path / "repo"
    nested = root / ".worktrees" / "feature"
    nested.mkdir(parents=True)
    manager = _FakeManager(
        [_state("rootone", root), _state("roottwo", root), _state("nestedws", nested)]
    )

    monkeypatch.chdir(nested)
    assert resolve_or_infer_workspace(manager, None).id == "nestedws"  # type: ignore[arg-type]


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


# ─── the compose mark ────────────────────────────────────────────────────────


def _compose_state(worktree: Path, *, owned: bool) -> WorkspaceState:
    """A workspace whose container is a compose stack, owned or not."""
    state = _state("stackws", worktree)
    state.runtime = Runtime.CONTAINER
    state.container = ContainerRuntimeState(
        container_id="c" * 64,
        compose_project="repo_devcontainer",
        compose_owned=owned,
    )
    return state


def test_an_owned_compose_stack_says_kill_takes_the_whole_thing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Mode and ownership are separate facts: a stack and a single container
    must not render identically everywhere a user could look."""
    _emit_runtime_marks(_compose_state(tmp_path, owned=True))

    out = capsys.readouterr().out
    assert "compose stack repo_devcontainer" in out
    assert "whole stack" in out
    assert "declared volumes are kept" in out


def test_an_unverified_compose_stack_warns_what_kill_will_leave_behind(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The consequence, before the user runs `kill` rather than after.

    Teardown degrades to label-filtered removal, which reaches the primary
    service only — so siblings keep running. That is worth a warning tone.
    """
    _emit_runtime_marks(_compose_state(tmp_path, owned=False))

    out = capsys.readouterr().out
    assert "unverified" in out
    assert "sibling services" in out


def test_a_single_container_workspace_carries_no_compose_mark(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Absence is the default — the mark names a mode, never the ordinary case."""
    state = _state("plain", tmp_path)
    state.runtime = Runtime.CONTAINER
    state.container = ContainerRuntimeState(container_id="c" * 64)

    _emit_runtime_marks(state)

    assert "compose" not in capsys.readouterr().out


# ─── the no-in-container-tmux mark ──────────────────────────────────────────


def test_a_container_with_no_tmux_binary_surfaces_the_degradation(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`tmux_command == ""` means Grove had no bundle for this image/arch.

    That is the ONLY trace of the degradation short of this mark — the
    workspace otherwise looks like a healthy container (no fallback reason,
    `runtime` stays CONTAINER) — so the mark must fire off the empty string,
    not off `runtime_fallback_reason`.
    """
    state = _state("bare-agent", tmp_path)
    state.runtime = Runtime.CONTAINER
    state.container = ContainerRuntimeState(container_id="c" * 64, tmux_command="")

    assert state.runtime_no_tmux is True
    _emit_runtime_marks(state)

    out = capsys.readouterr().out
    assert "no in-container tmux" in out
    assert "dies with your terminal" in out


def test_a_container_with_a_real_tmux_command_carries_no_degradation_mark(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """The ordinary containerized case: a resolved tmux binary, no warning."""
    state = _state("held-agent", tmp_path)
    state.runtime = Runtime.CONTAINER
    state.container = ContainerRuntimeState(container_id="c" * 64, tmux_command="tmux")

    assert state.runtime_no_tmux is False
    _emit_runtime_marks(state)

    assert "no in-container tmux" not in capsys.readouterr().out


def test_a_host_workspace_never_reads_no_tmux_even_with_a_stale_container_record() -> None:
    """`runtime is HOST` (e.g. a fallback workspace) always reads False.

    This is a CONTAINER-mode fact; a host workspace has no in-container tmux
    to speak of, degraded or otherwise, and must never surface the mark.
    """
    state = _state("host-ws", Path("/tmp/host-ws"))
    state.runtime = Runtime.HOST
    state.container = ContainerRuntimeState(container_id="c" * 64, tmux_command="")

    assert state.runtime_no_tmux is False


def test_runtime_no_tmux_view_field_derives_from_the_container_record(
    tmp_path: Path,
) -> None:
    """`WorkspaceStateView` mirrors the derived fact, not a persisted one."""
    state = _state("view-ws", tmp_path)
    state.runtime = Runtime.CONTAINER
    state.container = ContainerRuntimeState(container_id="c" * 64, tmux_command="")

    view = WorkspaceStateView.from_state(state)

    assert view.runtime_no_tmux is True
