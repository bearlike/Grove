"""``grove create`` / ``grove message`` Typer surface.

In-process via CliRunner against a real tmp git repo and the FakeTmux seam —
no daemon, no real tmux. Exercises the real engine create/steer path: only the
tmux/git I/O boundary is faked (via the shared ``fake_tmux`` fixture). Pins the
Auto + explicit-branch happy paths, the mutually-exclusive-flag guard, the
unknown-agent error surfaced cleanly, and message-to-a-resolved-id.

The :class:`BranchFlags` mapping is unit-tested directly (no CliRunner) since
it is the one piece of real logic the command shells out to.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from grove.core import (
    AutoBranch,
    ExistingLocalBranch,
    GroveError,
    NewNamedBranch,
    RootBranch,
    TrackRemoteBranch,
    build,
)
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.contracts.views import WorkspaceDefaultsView
from grove.core.git import GitRepo
from grove.core.manager import WorkspaceManager
from grove.core.tmux import ContainerAttach, HostAttach
from grove.core.workspace import Runtime, WorkspaceTitle
from grove.tui.cli import app
from grove.tui.cli_workspace import BranchFlags, QuickCreate
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


def test_create_model_flag_forwards_to_launch(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    """``--model`` rides ``CreateWorkspaceRequest.model`` verbatim onto the
    agent's launch decoration — Grove never validates the id, it just forwards
    it to the tool's ``--model`` flag."""
    del project
    result = runner.invoke(app, ["create", "model test", "--agent", "claude", "--model", "opus"])
    assert result.exit_code == 0, result.output
    _, decoration = fake_tmux.launch_decorations[-1]
    assert "--model" in decoration
    assert decoration[decoration.index("--model") + 1] == "opus"


def test_create_model_short_flag(runner: CliRunner, project: Path, fake_tmux: FakeTmux) -> None:
    """``-m`` is the short form of ``--model`` (verified free of collisions
    with the other create flags: -a/-b/-c/-t/-d/-p)."""
    del project
    result = runner.invoke(app, ["create", "model short", "--agent", "claude", "-m", "sonnet"])
    assert result.exit_code == 0, result.output
    _, decoration = fake_tmux.launch_decorations[-1]
    assert "--model" in decoration
    assert decoration[decoration.index("--model") + 1] == "sonnet"


def test_create_no_model_flag_omits_model_decoration(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    """Omitting ``--model`` leaves the launch decoration without a ``--model``
    flag — the tool falls back to its own default (never a Grove-picked one)."""
    del project
    result = runner.invoke(app, ["create", "no model", "--agent", "claude"])
    assert result.exit_code == 0, result.output
    _, decoration = fake_tmux.launch_decorations[-1]
    assert "--model" not in decoration


# ─── grove message ──────────────────────────────────────────────────────────


def test_message_resolves_prefix_and_sends(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    del project
    created = runner.invoke(app, ["create", "steer me", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    # Pull the new id off the `created <id>` line.
    new_id = _created_id(created)

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
    # Scan for the line rather than indexing line 0: the engine legitimately
    # logs before the result (a container runtime that is unavailable falls
    # back to the host *loudly*), and a helper that assumes the confirmation
    # is the first line turns any new log line into four unrelated failures.
    return _created_id(created)


def _created_id(created: Result) -> str:
    """The id off a `grove create` result, wherever the confirmation line sits.

    Scanning beats indexing line 0: the engine legitimately logs before the
    result (an unavailable container runtime falls back to the host
    *loudly*), and a helper that assumes the confirmation is the first
    line turns any new log line into a fistful of unrelated failures.
    """
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(f"no `created <id>` line in CLI output:\n{created.output}")


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
    inside = HostAttach(tmux_session="grove-x", inside_outer_tmux=True).terminal_argv()
    assert inside == ["tmux", "switch-client", "-t", "grove-x"]
    outside = HostAttach(tmux_session="grove-x", inside_outer_tmux=False).terminal_argv()
    assert outside == ["tmux", "attach", "-t", "grove-x"]


def test_container_attach_argv_is_the_engine_argv_verbatim() -> None:
    """No host tmux anywhere in it — the engine already composed the way in."""
    argv = ("devcontainer", "exec", "--workspace-folder", "/w", "--", "tmux", "new-session")
    assert ContainerAttach(argv=argv).terminal_argv() == list(argv)


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
    # A host workspace is on its own tmux server, so `-L <socket>` leads.
    assert args[:2] == ["tmux", "-L"] and args[3] == "attach" and args[4] == "-t"


# ─── resume-into-workspace (grove create --resume-session) ──────────────────


def test_create_resume_session_flag_emits_resume(
    runner: CliRunner,
    project: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--resume-session <id>` launches claude with `--resume <id>` (continue),
    not `--session-id` (fresh)."""
    claude_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    resume = "12345678-1111-2222-3333-444455556666"
    _write_transcript(claude_home, project, resume)  # must exist before resume resolves it
    result = runner.invoke(
        app, ["create", "resume me", "--agent", "claude", "--resume-session", resume]
    )
    assert result.exit_code == 0, result.output
    _, decoration = fake_tmux.launch_decorations[-1]
    assert decoration[:2] == ["--resume", resume]


def test_create_resume_session_rejected_for_shell(runner: CliRunner, project: Path) -> None:
    """A shell agent can't resume by id — clean one-line error, exit 1."""
    del project
    result = runner.invoke(app, ["create", "x", "--agent", "shell", "--resume-session", "a-b-c"])
    assert result.exit_code == 1
    assert "cannot resume" in result.output


# ─── grove sessions remap ────────────────────────────────────────────────────


def _write_transcript(claude_home: Path, cwd: Path, session_id: str) -> None:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text(
        f'{{"type":"user","cwd":"{cwd}"}}\n', encoding="utf-8"
    )


def test_sessions_remap_pins_discovered_session(
    runner: CliRunner,
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claude_home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    created = runner.invoke(app, ["create", "remap host", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    ws_id = _created_id(created)
    worktree = next(
        line.split("worktree:", 1)[1].strip()
        for line in created.output.splitlines()
        if "worktree:" in line
    )
    hand = "cafef00d-9999-8888-7777-666655554444"
    _write_transcript(claude_home, Path(worktree), hand)

    result = runner.invoke(app, ["sessions", "remap", ws_id[:8], "cafef00d"])
    assert result.exit_code == 0, result.output
    assert "remapped" in result.output
    assert hand in result.output


def test_sessions_remap_unknown_workspace_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["sessions", "remap", "deadbeef", "whatever"])
    assert result.exit_code == 1
    assert "no workspace matches" in result.output


def test_sessions_remap_unknown_session_error(runner: CliRunner, project: Path) -> None:
    del project
    created = runner.invoke(app, ["create", "host", "--agent", "claude"])
    ws_id = _created_id(created)
    result = runner.invoke(app, ["sessions", "remap", ws_id[:8], "no-such-session"])
    assert result.exit_code == 1
    assert "no session matches" in result.output


# ─── cwd binding (the build() seam) ───────────────────────────────────────────


def test_ls_outside_a_repo_errors_cleanly(
    runner: CliRunner,
    tmp_state_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`grove ls` outside any git repo is a typed one-line error, exit 1.

    `build(Path.cwd())` must not treat the cwd AS the repo root — that would
    list `[]` for a non-repo directory (and open the TUI empty) instead of
    surfacing the documented error.
    """
    del tmp_state_dir
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["ls"])
    assert result.exit_code == 1
    assert "git repository" in result.output


def test_build_from_linked_worktree_binds_to_main_root(
    tmp_state_dir: Path, tmp_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """From inside a linked worktree, build() keys the manager by the MAIN
    worktree root (the store's key), not the linked worktree's own root."""
    del tmp_state_dir
    linked = tmp_repo.parent / "linked-wt"
    GitRepo(tmp_repo).worktree_add(linked, new_branch="grove-linked-test")
    monkeypatch.chdir(linked)
    manager = build()
    assert manager.repo_root == tmp_repo.resolve()


# ─── QuickCreate: what an unset flag resolves to (pure, no CliRunner) ────────


def _defaults(**over: object) -> WorkspaceDefaultsView:
    base: dict[str, object] = {
        "agent": "claude",
        "runtime": "host",
        "brief": False,
        "model": None,
        "branch_mode": "auto",
        "base_ref": None,
        "skip_init": False,
    }
    return WorkspaceDefaultsView(**{**base, **over})  # type: ignore[arg-type]


def test_quick_create_mints_a_short_id_title_when_none_is_given() -> None:
    """The CLI uses the one rule every client shares, so the trigger recognises it."""
    title = QuickCreate(defaults=_defaults()).title(None)
    assert WorkspaceTitle.is_generated(title)


def test_quick_create_keeps_an_explicit_title() -> None:
    assert QuickCreate(defaults=_defaults()).title("fix login") == "fix login"


def test_branch_flags_leave_the_plan_to_the_engine_when_none_is_set() -> None:
    """No flag means no plan on the request, so saved defaults and the built-in apply."""
    assert not BranchFlags().any_set
    assert BranchFlags(base="origin/main").any_set
    assert BranchFlags(worktree=True).to_plan() == AutoBranch(base_ref="HEAD")
    assert BranchFlags(base="origin/main").to_plan() == AutoBranch(base_ref="origin/main")
    assert BranchFlags(branch="fix/login").to_plan() == NewNamedBranch(
        name="fix/login", base_ref="HEAD"
    )
    with pytest.raises(GroveError, match="mutually exclusive"):
        BranchFlags(worktree=True, root=True).to_plan()


# ─── the quick path end to end ───────────────────────────────────────────────


def test_bare_create_resolves_the_saved_defaults_and_mints_a_title(
    runner: CliRunner, project: Path
) -> None:
    """`grove create` with no arguments at all is the whole point of the path."""
    (project / ".grove").mkdir(exist_ok=True)
    (project / ".grove" / "config.json").write_text(
        json.dumps({"defaults": {"agent": "claude", "runtime": "host"}}), encoding="utf-8"
    )

    result = runner.invoke(app, ["create"])

    assert result.exit_code == 0, result.output
    assert "created " in result.output
    assert "title:    wk-" in result.output


@pytest.mark.launch_builtins
def test_bare_create_without_saved_defaults_uses_the_builtins(
    runner: CliRunner, project: Path
) -> None:
    """Nothing saved: the first agent, on the host, in the current checkout."""
    result = runner.invoke(app, ["create", "--no-attach"])

    assert result.exit_code == 0, result.output
    assert "runtime:  host" in result.output
    assert f"worktree: {project}" in result.output


def test_create_cwd_flag_starts_the_agent_in_a_repo_relative_subdir(
    runner: CliRunner, project: Path, fake_tmux: FakeTmux
) -> None:
    """`--cwd webapp` moves only the agent session; the worktree stays at the root."""
    sub = project / "webapp"
    sub.mkdir()
    (sub / ".keep").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=project, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add webapp", "--no-verify"],
        cwd=project,
        check=True,
        capture_output=True,
    )

    result = runner.invoke(app, ["create", "nested", "--agent", "claude", "--cwd", "webapp"])

    assert result.exit_code == 0, result.output
    state = build().list()[0]
    assert state.project_subpath == "webapp"
    worktree = Path(state.worktree_path)
    assert worktree.name != "webapp"  # the worktree itself is repo-root level
    assert fake_tmux.session_cwds[state.tmux_session] == worktree / "webapp"


def test_create_cwd_outside_the_repo_is_a_clean_error(runner: CliRunner, project: Path) -> None:
    del project
    result = runner.invoke(app, ["create", "out", "--agent", "claude", "--cwd", "../../elsewhere"])
    assert result.exit_code == 1
    assert "not within repo root" in result.output


# ─── grove create: the request it composes ─────────────────────────────────


def _capture_create(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Record the request the command composes, then stop before any launch."""
    seen: list[object] = []

    def fake_create(self: WorkspaceManager, request: object) -> object:
        seen.append(request)
        raise GroveError("stop after capture")

    monkeypatch.setattr(WorkspaceManager, "create", fake_create)
    return seen


@pytest.mark.parametrize(
    ("flags", "fields"),
    [
        (["--runtime", "host"], {"runtime": Runtime.HOST, "remote_runtime": None}),
        (["--runtime", "container"], {"runtime": Runtime.CONTAINER, "remote_runtime": None}),
        (
            ["--runtime", "sandbox", "--node", "edge-1"],
            {"runtime": Runtime.REMOTE, "remote_runtime": "sandbox", "node": "edge-1"},
        ),
    ],
)
def test_create_runtime_names_host_container_or_a_remote_block(
    runner: CliRunner,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    flags: list[str],
    fields: dict[str, object],
) -> None:
    del project
    seen = _capture_create(monkeypatch)
    result = runner.invoke(app, ["create", "t", "--agent", "claude", "--no-attach", *flags])
    assert seen, result.output
    (request,) = seen
    for name, value in fields.items():
        assert getattr(request, name) == value, name
