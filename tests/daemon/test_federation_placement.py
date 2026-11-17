"""How a hub names a create's project and the labels it requires."""

from __future__ import annotations

import subprocess
from pathlib import Path

from grove.core.config import GroveConfig, load_config
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.daemon.federation import create_resolver


def test_a_hub_create_takes_its_selector_from_the_projects_committed_config(
    tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The project's cascade default, so an orchestrator needs no new parameter."""
    del tmp_state_dir
    subprocess.run(
        ["git", "remote", "add", "origin", "git@git.example.com:org/repo.git"],
        cwd=tmp_repo,
        check=True,
    )
    (tmp_repo / ".grove").mkdir()
    (tmp_repo / ".grove" / "config.json").write_text(
        '{"scheduling": {"require": {"residency": "eu"}}}'
    )
    cfg = GroveConfig()
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(), config_loader=load_config)
    resolve = create_resolver(registry, cfg)
    base = CreateWorkspaceRequest(agent_name="claude", title="t", repo_root=tmp_repo)
    identity, selector = resolve(base)
    assert identity.remote == "git.example.com/org/repo"
    assert selector == {"residency": "eu"}
    # The request's own selector wins, including an explicit empty one.
    assert resolve(base.model_copy(update={"selector": {}}))[1] == {}
