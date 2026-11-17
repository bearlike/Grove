"""The runtime catalog and agent capabilities say what the engine accepts, never more.

A row a form can select must be exactly what a create accepts, so these drive
the engine with what the catalog offers rather than asserting the catalog
against a literal list.
"""

from __future__ import annotations

import dataclasses
import typing
from pathlib import Path

import pytest

from grove.core.config import (
    AgentSpec,
    EpisodicRuntimeConfig,
    GroveConfig,
    ResidentRuntimeConfig,
)
from grove.core.contracts.agents import AgentSummaryView
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import WorkspaceStateView
from grove.core.launch_options import node_options, runtime_options
from grove.core.manager import WorkspaceManager
from grove.core.runtimes import RemoteHandle
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime
from tests.conftest import FakeCli, FakePreflight, FakeTmux

pytestmark = pytest.mark.launch_builtins

_BLOCKS = {
    "sbx": {"kind": "opensandbox", "image": "img:1", "resources": {"cpu": "1"}},
    "turns": {"kind": "lambda_episodic", "region": "r", "function": "f", "image": "img:1"},
}


def _cfg(tmp_path: Path, **extra: object) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "test/"},
            "tmux": {"session_prefix": "test-"},
            "agents": [{"name": "first", "command": "claude", "kind": "claude_code"}],
            "runtimes": _BLOCKS,
            **extra,
        }
    )


def test_every_supported_driver_appears_once_configured_or_not(tmp_path: Path) -> None:
    """The driver set is read off the config unions, so a new driver cannot be left out."""
    options = runtime_options(_cfg(tmp_path), host_unavailable=None, container_unavailable=None)

    drivers = [o.driver for o in options if o.kind == "remote"]
    supported = {
        c.model_fields["kind"].default
        for union in (ResidentRuntimeConfig, EpisodicRuntimeConfig)
        for c in typing.get_args(union)
    }
    assert sorted(drivers) == sorted(supported)
    assert len({o.id for o in options}) == len(options)


def test_configured_blocks_keep_their_exact_names_and_unset_drivers_are_not_sendable(
    tmp_path: Path,
) -> None:
    options = {
        o.id: o
        for o in runtime_options(_cfg(tmp_path), host_unavailable=None, container_unavailable=None)
    }

    assert options["sbx"].status == "unknown"
    assert options["sbx"].workspace_target is True
    assert options["turns"].workspace_target is False
    unset = options["driver:aca_job"]
    assert unset.status == "not_configured"
    assert unset.setup is not None and "kind: aca_job" in unset.setup


def test_readiness_is_reported_as_measured_and_never_invented(tmp_path: Path) -> None:
    options = {
        o.id: o
        for o in runtime_options(
            _cfg(tmp_path), host_unavailable="tmux missing", container_unavailable=None
        )
    }

    assert (options["host"].status, options["host"].reason) == ("unavailable", "tmux missing")
    assert options["container"].status == "ready"
    assert all(o.status != "ready" for o in options.values() if o.kind == "remote")


def test_a_hubs_node_rows_are_offered_by_name_with_readiness_unknown() -> None:
    rows = node_options("eu-node", ["sbx"])

    assert [(r.id, r.node, r.status) for r in rows] == [
        ("host", "eu-node", "unknown"),
        ("container", "eu-node", "unknown"),
        ("sbx", "eu-node", "unknown"),
    ]


def test_selectable_rows_are_what_a_create_accepts(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    """Each selectable local row, sent through runtime_fields, is the runtime the create records."""
    cfg = _cfg(tmp_path)
    cli = FakeCli()
    manager = WorkspaceManager(
        repo_root=tmp_repo,
        cfg=cfg,
        store=JsonWorkspaceStore(path=tmp_path / "state.json"),
        devcontainer_cli=cli,
        preflight=FakePreflight(devcontainer_cli=cli),  # type: ignore[arg-type]
    )
    options = runtime_options(cfg, host_unavailable=None, container_unavailable=None)

    for option in (o for o in options if o.kind in ("host", "container")):
        state = manager.create(
            CreateWorkspaceRequest(
                title=f"via-{option.id}", **CreateWorkspaceRequest.runtime_fields(option.id)
            )
        )
        assert state.runtime is Runtime(option.id)


@pytest.mark.parametrize(
    ("kind", "native", "modes"),
    [
        ("claude_code", True, ("native", "terminal")),
        ("codex", False, ("native", "terminal")),
        ("generic", True, ("terminal",)),
    ],
)
def test_launch_modes_agree_with_the_engines_own_gate(
    kind: str, native: bool, modes: tuple[str, ...]
) -> None:
    """A mode is offered exactly when ``native_for`` would honour asking for it."""
    spec = AgentSpec.model_validate({"name": "a", "command": "x", "kind": kind, "native": native})
    view = AgentSummaryView.from_spec(spec)

    assert view.launch_modes == modes
    assert ("native" in modes) is spec.native_for(True)
    assert ("terminal" in modes) is (not spec.native_for(False))
    assert ("remote" in view.runtimes) is spec.native_for(True)


def test_the_state_view_names_the_working_directory_and_remote_block(
    tmp_repo: Path, fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    (tmp_repo / "webapp").mkdir()
    manager = WorkspaceManager(
        repo_root=tmp_repo, cfg=_cfg(tmp_path), store=JsonWorkspaceStore(path=tmp_path / "s.json")
    )
    state = manager.create(CreateWorkspaceRequest(title="nested", project_cwd=Path("webapp")))

    view = WorkspaceStateView.from_state(state)
    # A record that really carries a remote handle: on a host record the field
    # is None whether or not it is read, so only this can show it is.
    remote = WorkspaceStateView.from_state(
        dataclasses.replace(
            state, runtime=Runtime.REMOTE, remote=RemoteHandle(runtime="sbx", ref="exec-1")
        )
    )

    assert (view.project_subpath, view.remote_runtime) == ("webapp", None)
    assert remote.remote_runtime == "sbx"


def test_every_driver_names_itself_for_a_person(tmp_path: Path) -> None:
    """A form shows ``driver_label``, so no driver may reach one with only its config keyword."""
    options = runtime_options(_cfg(tmp_path), host_unavailable=None, container_unavailable=None)

    remote = [o for o in options if o.kind == "remote"]
    assert remote
    for option in remote:
        assert option.driver_label, option.id
        assert option.driver_label != option.driver
        assert "_" not in option.driver_label
    assert {o.id: o.driver_label for o in options}["sbx"] == "OpenSandbox"
    assert all(o.driver_label is None for o in options if o.kind != "remote")
