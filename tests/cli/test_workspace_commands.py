"""``grove create`` / ``grove message`` Typer surface (issue #45).

In-process via CliRunner against a real tmp git repo and the FakeTmux seam —
no daemon, no real tmux. Exercises the real engine create/steer path: only the
tmux/git I/O boundary is faked (via the shared ``fake_tmux`` fixture). Pins the
Auto + explicit-branch happy paths, the mutually-exclusive-flag guard, the
unknown-agent error surfaced cleanly, and message-to-a-resolved-id.

The :class:`BranchFlags` mapping is unit-tested directly (no CliRunner) since
it is the one piece of real logic the command shells out to.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove.core import (
    AttachInstruction,
    AutoBranch,
    ExistingLocalBranch,
    GroveError,
    NewNamedBranch,
    RootBranch,
    TrackRemoteBranch,
)
from grove.tui.cli import app
from grove.tui.cli_workspace import BranchFlags, _attach_argv
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


def _branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "branch", "--list", "--format=%(refname:short)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


# ─── BranchFlags mapping (pure, no CliRunner) ───────────────────────────────


def test_branch_flags_default_is_auto() -> None:
    plan = BranchFlags().to_plan()
    assert isinstance(plan, AutoBranch)
    assert plan.base_ref == "HEAD"


def test_branch_flags_auto_honors_base() -> None:
    plan = BranchFlags(base="origin/main").to_plan()
    assert isinstance(plan, AutoBranch)
    assert plan.base_ref == "origin/main"


def test_branch_flags_new_named_with_base() -> None:
    plan = BranchFlags(branch="feature/x", base="origin/main").to_plan()
    assert isinstance(plan, NewNamedBranch)
    assert plan.name == "feature/x"
    assert plan.base_ref == "origin/main"


def test_branch_flags_checkout_and_track_and_root() -> None:
    assert isinstance(BranchFlags(checkout="wip").to_plan(), ExistingLocalBranch)
    assert isinstance(BranchFlags(track="origin/wip").to_plan(), TrackRemoteBranch)
    assert isinstance(BranchFlags(root=True).to_plan(), RootBranch)


def test_branch_flags_mutually_exclusive() -> None:
    with pytest.raises(GroveError, match="mutually exclusive"):
        BranchFlags(branch="a", checkout="b").to_plan()


def test_branch_flags_base_rejected_with_checkout() -> None:
    with pytest.raises(GroveError, match="--base has no effect"):
        BranchFlags(checkout="wip", base="HEAD").to_plan()


# ─── grove create ───────────────────────────────────────────────────────────


def test_create_auto_branch(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(app, ["create", "fix login", "--agent", "claude"])
    assert result.exit_code == 0, result.output
    assert "created " in result.output
    assert "branch:" in result.output
    assert "worktree:" in result.output
    # Auto branch is slugged off the title with the default grove/ prefix.
    created = {b for b in _branches(project) if "fix-login" in b}
    assert created, _branches(project)


def test_create_explicit_branch(runner: CliRunner, project: Path) -> None:
    result = runner.invoke(
        app, ["create", "fix login", "--agent", "claude", "--branch", "fix/login"]
    )
    assert result.exit_code == 0, result.output
    assert "fix/login" in result.output
    assert "fix/login" in _branches(project)


def test_create_mutually_exclusive_flags_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(
        app,
        ["create", "x", "--agent", "claude", "--branch", "a", "--checkout", "b"],
    )
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_create_unknown_agent_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["create", "x", "--agent", "nope"])
    assert result.exit_code == 1
    assert "unknown agent" in result.output


# ─── grove message ──────────────────────────────────────────────────────────


def test_message_resolves_prefix_and_sends(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    del project
    created = runner.invoke(app, ["create", "steer me", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    # Pull the new id off the `created <id>` line.
    new_id = created.output.splitlines()[0].split("created ", 1)[1].strip()

    result = runner.invoke(app, ["message", new_id[:8], "run the tests"])
    assert result.exit_code == 0, result.output
    assert "sent to" in result.output
    # The steer text reached the fake tmux send_text seam.
    assert any(text == "run the tests" for _, text in fake_tmux.sent_texts)


def test_message_unknown_workspace_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["message", "deadbeef", "hi"])
    assert result.exit_code == 1
    assert "no workspace matches" in result.output


# ─── lifecycle verbs (pause / resume / respawn / kill) — CLI↔TUI↔MCP parity ──


def _create(runner: CliRunner, title: str = "work") -> str:
    """Create a workspace via the CLI and return its id (helper for the verbs)."""
    created = runner.invoke(app, ["create", title, "--agent", "claude"])
    assert created.exit_code == 0, created.output
    return created.output.splitlines()[0].split("created ", 1)[1].strip()


def test_pause_then_resume(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    paused = runner.invoke(app, ["pause", ws_id[:8]])
    assert paused.exit_code == 0, paused.output
    assert "paused" in paused.output

    resumed = runner.invoke(app, ["resume", ws_id[:8]])
    assert resumed.exit_code == 0, resumed.output
    assert "resumed" in resumed.output


def test_kill_removes_workspace(runner: CliRunner, project: Path) -> None:
    del project
    ws_id = _create(runner)

    killed = runner.invoke(app, ["kill", ws_id[:8], "--yes"])
    assert killed.exit_code == 0, killed.output
    assert "killed" in killed.output
    # Gone from the repo's listing — a second resolve can't find it.
    again = runner.invoke(app, ["kill", ws_id[:8], "--yes"])
    assert again.exit_code == 1
    assert "no workspace matches" in again.output


def test_kill_aborts_on_decline(runner: CliRunner, project: Path) -> None:
    """Without --yes, declining the confirmation leaves the workspace intact."""
    del project
    ws_id = _create(runner)

    declined = runner.invoke(app, ["kill", ws_id[:8]], input="n\n")
    assert declined.exit_code != 0  # typer.Abort
    # Still resolvable → pause succeeds, proving it was never killed.
    assert runner.invoke(app, ["pause", ws_id[:8]]).exit_code == 0


def test_respawn_refuses_a_running_workspace(runner: CliRunner, project: Path) -> None:
    """The CLI shell surfaces the engine's precondition error (respawn needs an
    OFFLINE workspace) as a clean non-zero exit — the engine owns the rule."""
    del project
    ws_id = _create(runner)
    result = runner.invoke(app, ["respawn", ws_id[:8]])
    assert result.exit_code == 1
    assert "no workspace matches" not in result.output  # it resolved; the engine refused


def test_lifecycle_unknown_workspace_error(runner: CliRunner, project: Path) -> None:
    del project
    for verb in ("pause", "resume", "respawn", "kill", "attach"):
        result = runner.invoke(
            app, [verb, "deadbeef", "--yes"] if verb == "kill" else [verb, "deadbeef"]
        )
        assert result.exit_code == 1, (verb, result.output)
        assert "no workspace matches" in result.output, (verb, result.output)


# ─── grove attach ─────────────────────────────────────────────────────────────


def test_attach_argv_switch_inside_tmux_else_attach() -> None:
    inside = _attach_argv(AttachInstruction(tmux_session="grove-x", inside_outer_tmux=True))
    assert inside == ["tmux", "switch-client", "-t", "grove-x"]
    outside = _attach_argv(AttachInstruction(tmux_session="grove-x", inside_outer_tmux=False))
    assert outside == ["tmux", "attach", "-t", "grove-x"]


def test_attach_resolves_then_execs_tmux(
    runner: CliRunner, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """attach hands the terminal to tmux via execvp — patch the exec seam and
    assert the resolved session's tmux command is what we'd hand off."""
    del project
    ws_id = _create(runner)
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "grove.tui.cli_workspace.os.execvp",
        lambda file, args: captured.update(file=file, args=args),
    )
    result = runner.invoke(app, ["attach", ws_id[:8]])
    assert result.exit_code == 0, result.output
    assert captured["file"] == "tmux"
    args = captured["args"]
    assert isinstance(args, list)
    assert args[0] == "tmux" and args[1] in {"attach", "switch-client"} and args[2] == "-t"
