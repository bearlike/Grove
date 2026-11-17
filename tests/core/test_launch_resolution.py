"""One launch resolution for the preview, the defaults view and the create.

These pin the product built-ins (host, current checkout), so the module opts
out of the suite's worktree pin with ``launch_builtins``. Each create test
asserts the recorded workspace against the resolver's own answer, never each
against a literal, because two literals agreeing is how a form and a create
came to disagree in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import AutoBranch, ExistingLocalBranch, RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceDefaultsView
from grove.core.errors import InvalidLaunch
from grove.core.launch_resolution import LaunchResolver
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Placement, Runtime
from tests.conftest import FakeCli, FakePreflight, FakeTmux

pytestmark = pytest.mark.launch_builtins


def _cfg(tmp_path: Path, **defaults: object) -> GroveConfig:
    """Containers ENABLED, the shipped setting under which nothing should pick them."""
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True},
            "agents": [
                {"name": "first", "command": "claude", "kind": "claude_code"},
                {"name": "second", "command": "codex", "kind": "codex"},
            ],
            "runtimes": {
                "sbx": {"kind": "opensandbox", "image": "img:1", "resources": {"cpu": "1"}},
            },
            "defaults": defaults,
        }
    )


def _manager(tmp_repo: Path, cfg: GroveConfig, tmp_path: Path) -> WorkspaceManager:
    cli = FakeCli()
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(devcontainer_cli=cli),  # type: ignore[arg-type]
    )


def test_an_empty_request_resolves_to_the_builtins(tmp_path: Path) -> None:
    launch = LaunchResolver.resolve(_cfg(tmp_path), CreateWorkspaceRequest())

    assert launch.agent is not None and launch.agent.name == "first"
    assert launch.runtime is Runtime.HOST
    assert launch.branch_plan == RootBranch()
    assert launch.problems == ()
    assert launch.sources["agent_name"] == "config"
    assert launch.sources["runtime"] == "builtin"
    assert launch.sources["branch_plan"] == "builtin"


def test_an_omitted_create_runs_on_the_host_in_the_current_checkout(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Configuring containers and a remote runtime selects neither."""
    cfg = _cfg(tmp_path)
    state = _manager(tmp_repo, cfg, tmp_path).create(CreateWorkspaceRequest(title="bare"))

    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is None
    assert state.placement is Placement.ROOT
    assert Path(state.worktree_path) == tmp_repo
    view = WorkspaceDefaultsView.from_config(cfg)
    assert (view.agent, view.runtime, view.branch_mode) == (
        state.agent_name,
        state.runtime.value,
        state.placement.value,
    )


def test_saved_defaults_and_explicit_requests_keep_their_order(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, agent="second", runtime="container", branch_mode="auto", base_ref="main")

    saved = LaunchResolver.resolve(cfg, CreateWorkspaceRequest())
    explicit = LaunchResolver.resolve(
        cfg,
        CreateWorkspaceRequest(agent_name="first", runtime=Runtime.HOST, branch_plan=RootBranch()),
    )

    assert saved.agent is not None and saved.agent.name == "second"
    assert saved.runtime is Runtime.CONTAINER
    assert saved.branch_plan == AutoBranch(base_ref="main")
    assert explicit.agent is not None and explicit.agent.name == "first"
    assert explicit.runtime is Runtime.HOST
    assert explicit.branch_plan == RootBranch()
    assert {explicit.sources[k] for k in ("agent_name", "runtime", "branch_plan")} == {"request"}


def test_an_explicit_plan_keeps_its_meaning_under_a_saved_root_default(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    cfg = _cfg(tmp_path, branch_mode="root")
    state = _manager(tmp_repo, cfg, tmp_path).create(
        CreateWorkspaceRequest(title="isolated", branch_plan=AutoBranch())
    )

    assert state.placement is Placement.WORKTREE
    assert state.branch.startswith("test/isolated-")


@pytest.mark.parametrize("mode", ["new", "existing", "remote"])
def test_a_saved_named_branch_mode_is_a_problem_not_a_new_branch(
    mode: str, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A saved mode that needs a name never silently becomes a fresh auto branch."""
    cfg = _cfg(tmp_path, branch_mode=mode)

    launch = LaunchResolver.resolve(cfg, CreateWorkspaceRequest())
    assert launch.branch_plan is None
    assert [(p.field, p.error) for p in launch.problems] == [("branch_plan", "incomplete_default")]

    manager = _manager(tmp_repo, cfg, tmp_path)
    with pytest.raises(InvalidLaunch) as refused:
        manager.create(CreateWorkspaceRequest(title="named"))
    assert refused.value.field == "branch_plan"
    assert manager.list() == []
    # The same saved mode is satisfied by a request that names its branch.
    named = LaunchResolver.resolve(
        cfg, CreateWorkspaceRequest(branch_plan=ExistingLocalBranch(name="main"))
    )
    assert named.problems == ()


def test_an_unknown_agent_is_refused_before_any_side_effect(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    manager = _manager(tmp_repo, _cfg(tmp_path), tmp_path)

    with pytest.raises(InvalidLaunch, match="unknown agent: nope") as refused:
        manager.create(CreateWorkspaceRequest(agent_name="nope"))

    assert refused.value.field == "agent_name"
    assert manager.list() == []
    assert fake_tmux.sessions == set()


def test_a_remote_runtime_puts_an_implicit_root_launch_on_an_auto_branch(tmp_path: Path) -> None:
    """Removing the implicit-plan adjustment would send a remote worker into the root checkout."""
    cfg = _cfg(tmp_path)

    implicit = LaunchResolver.resolve(cfg, CreateWorkspaceRequest(runtime=Runtime.REMOTE))
    explicit_root = LaunchResolver.resolve(
        cfg, CreateWorkspaceRequest(runtime=Runtime.REMOTE, branch_plan=RootBranch())
    )

    assert implicit.problems == ()
    assert implicit.branch_plan == AutoBranch(base_ref="HEAD")
    assert [(item.field, item.value) for item in implicit.adjustments] == [("branch_plan", "auto")]
    assert [(p.field, p.error) for p in explicit_root.problems] == [
        ("branch_plan", "remote_requires_worktree")
    ]


def test_an_explicit_container_is_refused_when_unavailable_not_downgraded(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """The arm-4 host fallback is for a saved default; an explicit choice is honoured or refused."""
    cfg = _cfg(tmp_path)
    cli = FakeCli()
    preflight = FakePreflight(ready=False, devcontainer_cli=cli)
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=preflight,  # type: ignore[arg-type]
    )

    with pytest.raises(InvalidLaunch) as refused:
        manager.create(CreateWorkspaceRequest(runtime=Runtime.CONTAINER))
    assert (refused.value.field, refused.value.error) == ("runtime", "runtime_unavailable")
    assert manager.list() == []

    saved = _cfg(tmp_path, runtime="container")
    fallback = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=saved,
        store=JsonWorkspaceStore(path=tmp_path / "saved.json"),
        devcontainer_cli=cli,
        preflight=preflight,  # type: ignore[arg-type]
    ).create(CreateWorkspaceRequest(title="saved"))
    assert fallback.runtime is Runtime.HOST
    assert fallback.runtime_fallback_reason


def test_the_defaults_view_carries_no_secret_or_host_path(tmp_path: Path) -> None:
    """The view is a set of choices and their sources; nothing it adds is a path or a value."""
    cfg = _cfg(tmp_path)
    dumped = WorkspaceDefaultsView.from_config(cfg).model_dump_json()

    assert str(tmp_path) not in dumped
    assert "img:1" not in dumped
