"""A project's cross-node name: its remote, normalized, plus its default branch."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.errors import ProjectNotOnNode
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore


@pytest.mark.parametrize(
    "url",
    [
        "git@git.example.com:org/repo.git",
        "ssh://git@Git.Example.com:2222/org/repo",
        "https://user@git.example.com/org/repo.git/",
        "https://git.example.com/org/repo",
    ],
)
def test_every_clone_url_form_names_the_same_project(url: str) -> None:
    assert ProjectIdentity.normalize_remote(url) == "git.example.com/org/repo"


@pytest.mark.parametrize("url", ["/srv/repo.git", "../repo", "https://git.example.com/"])
def test_a_remote_naming_no_host_names_no_project(url: str) -> None:
    """A path remote is the per-node fact the identity exists to replace."""
    assert ProjectIdentity.normalize_remote(url) is None


def test_each_node_resolves_the_identity_to_its_own_path(
    tmp_state_dir: Path, tmp_repo: Path, tmp_path: Path
) -> None:
    del tmp_state_dir
    subprocess.run(
        ["git", "remote", "add", "origin", "git@git.example.com:org/repo.git"],
        cwd=tmp_repo,
        check=True,
    )
    registry = RepoRegistry(
        cfg=GroveConfig.model_validate({"projects": [str(tmp_repo)]}),
        store=JsonWorkspaceStore(),
    )
    identity = registry.identity(tmp_repo)
    assert identity == ProjectIdentity(remote="git.example.com/org/repo", default_branch="main")
    # Another node's spelling of the same remote, and a different branch name.
    named = ProjectIdentity(remote="git.example.com/org/repo", default_branch="trunk")
    assert registry.root_for(named) == tmp_repo
    assert registry.known_identities() == [identity]
    with pytest.raises(ProjectNotOnNode):
        registry.root_for(named.model_copy(update={"remote": "git.example.com/org/other"}))
