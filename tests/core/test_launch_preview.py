"""The launch preview and the create it previews are one answer, from one config.

Every assertion compares the preview against what ``create`` actually did or
refused, never each against a literal, because two literals agreeing is how a
form and a create came to disagree. The module sees the product built-ins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from grove.core import paths as paths_mod
from grove.core.config import GroveConfig, load_config
from grove.core.contracts.branch_plan import AutoBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceDefaultsView
from grove.core.devcontainer import DevcontainerConfig
from grove.core.errors import ConfigError, GroveError, InvalidLaunch
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Placement, ProvisionStatus, Runtime
from tests.conftest import FakeCli, FakePreflight, FakeTmux

pytestmark = pytest.mark.launch_builtins

_AGENTS = [
    {"name": "first", "command": "claude", "kind": "claude_code"},
    {"name": "second", "command": "codex", "kind": "codex"},
]


def _cfg(tmp_path: Path, **defaults: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "container": {"enabled": True},
            "agents": _AGENTS,
            "defaults": defaults,
        }
    )


def _manager(
    tmp_repo: Path,
    tmp_path: Path,
    cfg: GroveConfig,
    *,
    ready: bool = True,
    store: JsonWorkspaceStore | None = None,
    loader: object = None,
) -> WorkspaceManager:
    cli = FakeCli(available=ready)
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=store or JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(ready=ready, devcontainer_cli=cli),  # type: ignore[arg-type]
        config_loader=loader,  # type: ignore[arg-type]
    )


def _preview(mgr: WorkspaceManager, request: CreateWorkspaceRequest):  # type: ignore[no-untyped-def]
    return mgr.preview(request, container_unavailable=mgr.container_unavailable_reason())


@pytest.mark.parametrize(
    ("defaults", "request_fields", "ready"),
    [
        ({}, {}, True),
        ({"runtime": "container"}, {}, False),
        ({"branch_mode": "auto", "base_ref": "HEAD"}, {}, True),
        ({}, {"runtime": Runtime.HOST, "branch_plan": AutoBranch()}, True),
        ({"agent": "second"}, {"agent_name": "first"}, True),
    ],
)
def test_the_preview_records_what_the_create_records(
    defaults: dict[str, object],
    request_fields: dict[str, object],
    ready: bool,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    mgr = _manager(tmp_repo, tmp_path, _cfg(tmp_path, **defaults), ready=ready)
    request = CreateWorkspaceRequest(title="same", **request_fields)  # type: ignore[arg-type]

    preview = _preview(mgr, request)
    state = mgr.create(request)

    assert preview.problems == ()
    assert preview.launch.agent is not None
    assert preview.launch.agent.name == state.agent_name
    assert preview.runtime is state.runtime
    assert preview.runtime_fallback_reason == state.runtime_fallback_reason
    assert preview.native is state.native
    assert preview.project_subpath == state.project_subpath
    placement = Placement.ROOT if preview.launch.branch_plan.kind == "root" else Placement.WORKTREE  # type: ignore[union-attr]
    assert placement is state.placement


@pytest.mark.parametrize(
    ("request_fields", "field"),
    [
        ({"agent_name": "nope"}, "agent_name"),
        ({"effort": "galactic"}, "effort"),
        ({"runtime": Runtime.CONTAINER}, "runtime"),
        ({"runtime": Runtime.REMOTE, "branch_plan": AutoBranch()}, "remote_runtime"),
        ({"runtime": Runtime.REMOTE}, "remote_runtime"),
        ({"project_cwd": Path("/elsewhere")}, "project_cwd"),
    ],
)
def test_every_refusal_the_create_raises_is_a_preview_problem_on_the_same_field(
    request_fields: dict[str, object],
    field: str,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
    tmp_path: Path,
) -> None:
    """A preview that passes a request the create then refuses is the bug this route prevents."""
    mgr = _manager(tmp_repo, tmp_path, _cfg(tmp_path), ready=False)
    request = CreateWorkspaceRequest(title="refused", **request_fields)  # type: ignore[arg-type]

    preview = _preview(mgr, request)

    assert [p.field for p in preview.problems][:1] == [field]
    with pytest.raises(GroveError):
        mgr.create(request)
    assert mgr.list() == []


def test_the_preview_runs_no_probe_when_handed_a_reading(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A per-keystroke preview must not shell out; the caller's reading decides."""
    mgr = _manager(tmp_repo, tmp_path, _cfg(tmp_path, runtime="container"), ready=True)

    stale = mgr.preview(CreateWorkspaceRequest(), container_unavailable="docker is down")

    assert stale.runtime is Runtime.HOST
    assert stale.runtime_fallback_reason == "docker is down"


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_a_saved_default_change_reaches_view_preview_and_create_without_a_rebuild(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    _write(project, {"agents": _AGENTS, "defaults": {"agent": "first"}})
    mgr = _manager(
        tmp_repo,
        tmp_path,
        load_config(tmp_repo, env={}),
        loader=lambda root: load_config(root, env={}),
    )

    _write(project, {"agents": _AGENTS, "defaults": {"agent": "second"}})
    view = WorkspaceDefaultsView.from_config(mgr.launch_config())
    preview = _preview(mgr, CreateWorkspaceRequest())
    state = mgr.create(CreateWorkspaceRequest(title="fresh"))

    assert view.agent == "second"
    assert view.sources["agent_name"] == "project"
    assert preview.launch.agent is not None and preview.launch.agent.name == "second"
    assert state.agent_name == "second"


def test_a_saved_agent_known_only_on_disk_refuses_before_the_cached_manager_launches(
    tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Deleting cached-roster validation would let a freshly saved bad default launch."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    _write(project, {"agents": _AGENTS})
    mgr = _manager(
        tmp_repo,
        tmp_path,
        load_config(tmp_repo, env={}),
        loader=lambda root: load_config(root, env={}),
    )

    newcomer = {"name": "newcomer", "command": "claude", "kind": "claude_code"}
    _write(project, {"agents": [*_AGENTS, newcomer], "defaults": {"agent": "newcomer"}})

    with pytest.raises(ConfigError, match=re.escape("defaults.agent")):
        mgr.launch_config()
    with pytest.raises(ConfigError, match=re.escape("defaults.agent")):
        mgr.create(CreateWorkspaceRequest(title="newcomer"))
    assert mgr.list() == []


def test_an_explicit_unknown_agent_is_a_launch_problem_not_a_config_problem(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """A request remains a launch-time choice, so the defaults view stays loadable."""
    mgr = _manager(tmp_repo, tmp_path, _cfg(tmp_path))
    request = CreateWorkspaceRequest(agent_name="newcomer", title="newcomer")

    view = WorkspaceDefaultsView.from_config(mgr.launch_config())
    preview = _preview(mgr, request)

    assert view.problems == ()
    assert [(p.field, p.error) for p in preview.problems] == [("agent_name", "unknown_agent")]
    with pytest.raises(InvalidLaunch) as refused:
        mgr.create(request)
    assert (refused.value.field, refused.value.error) == ("agent_name", "unknown_agent")
    assert mgr.list() == []


def test_respawn_still_promotes_a_saved_container_default_that_fell_back(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Promotion re-asks for the container the RECORD wanted, never for today's default.

    The record is the authority: a fallback reason means this workspace wanted
    a container. Asking "the default" instead answers host the moment nothing
    saved still selects containers (the built-in), so the respawn here runs
    under a config whose saved default is gone, which is the only state that
    tells the two apart.
    """
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    state = _manager(
        tmp_repo, tmp_path, _cfg(tmp_path, runtime="container"), ready=False, store=store
    ).create(CreateWorkspaceRequest(title="promote-me"))
    assert state.runtime is Runtime.HOST
    assert state.runtime_fallback_reason is not None

    fake_tmux.kill_session(state.tmux_session)
    promoted = _manager(tmp_repo, tmp_path, _cfg(tmp_path), ready=True, store=store).respawn(
        state.id
    )

    assert promoted.runtime is Runtime.CONTAINER
    assert promoted.runtime_fallback_reason is None
    assert promoted.provision_status is ProvisionStatus.OK


# ─── the three refusals that used to escape as a 500 with no field ───────────


def _forbidding_host(tmp_repo: Path, tmp_path: Path) -> WorkspaceManager:
    """A project whose committed devcontainer forbids running its agents on the host."""
    target = tmp_repo / ".devcontainer" / "devcontainer.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    customizations = {"grove": {"requires_container": True}}
    target.write_text(
        json.dumps({"image": "python:3.12", "customizations": customizations}), encoding="utf-8"
    )
    cli = FakeCli(
        config=DevcontainerConfig.model_validate(
            {"image": "python:3.12", "customizations": customizations}
        )
    )
    return WorkspaceManager(
        repo_root=tmp_repo,
        cfg=_cfg(tmp_path),
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(devcontainer_cli=cli),  # type: ignore[arg-type]
    )


def _agree(mgr: WorkspaceManager, request: CreateWorkspaceRequest) -> tuple[str, str]:
    """The (error, field) the preview reports, asserted equal to what create raises."""
    preview = mgr.preview(
        request,
        container_unavailable=mgr.container_unavailable_reason(),
        host_refused=mgr.host_refusal(),
    )
    with pytest.raises(InvalidLaunch) as refused:
        mgr.create(request)
    assert mgr.list() == []
    shown = [(p.error, p.field) for p in preview.problems]
    raised = (refused.value.error, refused.value.field)
    assert shown[:1] == [raised]
    return raised


def test_requires_container_is_the_same_runtime_refusal_in_preview_and_create(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _forbidding_host(tmp_repo, tmp_path)

    assert _agree(mgr, CreateWorkspaceRequest(runtime=Runtime.HOST)) == (
        "container_required",
        "runtime",
    )
    assert fake_tmux.sessions == set()


def test_an_out_of_repo_cwd_is_the_same_refusal_in_preview_and_create(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    mgr = _manager(tmp_repo, tmp_path, _cfg(tmp_path))

    assert _agree(mgr, CreateWorkspaceRequest(project_cwd=Path("/elsewhere"))) == (
        "project_cwd_outside_repo",
        "project_cwd",
    )
