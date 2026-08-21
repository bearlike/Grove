"""GET and PUT /defaults — resolved, scoped create-form answers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core import paths as paths_mod
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path) -> Iterator[TestClient]:
    del tmp_state_dir, fake_tmux
    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})
    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as client:
        yield client


def _defaults_body(**overrides: object) -> dict[str, object]:
    """Complete defaults replacement body, with explicit clears by default."""
    return {
        "agent": None,
        "runtime": None,
        "brief": None,
        "model": None,
        "branch_mode": None,
        "base_ref": None,
        "skip_init": None,
        **overrides,
    }


def test_get_defaults_resolves_the_repo_cascade(
    daemon: TestClient, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """The remote form receives the same selected answers as an in-process create form."""
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        json.dumps(
            {
                "container": {"enabled": False},
                "brief": {"enabled": False},
                "defaults": {"agent": "claude", "model": "user-model"},
            }
        ),
        encoding="utf-8",
    )
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(
        json.dumps({"defaults": {"agent": "codex", "base_ref": "main"}}),
        encoding="utf-8",
    )

    response = daemon.get(f"/defaults?repo={tmp_repo}")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "agent": "claude",
        "runtime": "host",
        "brief": False,
        "model": "user-model",
        "branch_mode": "auto",
        "base_ref": "main",
        "skip_init": False,
        # Read-only enrichment: the catalog rides the GET and is deliberately
        # absent from the PUT shape, so a repo declaring none answers empty
        # rather than omitting the keys.
        "agent_cwds": [],
        "agent_cwd": None,
    }


def test_put_defaults_writes_the_named_layer_and_get_reflects_it(
    daemon: TestClient, tmp_repo: Path
) -> None:
    body = _defaults_body(
        agent="codex",
        runtime="host",
        brief=False,
        model="gpt-5.4",
        branch_mode="existing",
        base_ref="develop",
        skip_init=True,
    )

    response = daemon.put(f"/defaults?repo={tmp_repo}&scope=project", json=body)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "path": str(paths_mod.project_config_path(tmp_repo)),
        "shadowed": [],
    }
    assert json.loads(paths_mod.project_config_path(tmp_repo).read_text(encoding="utf-8")) == {
        "defaults": body
    }

    resolved = daemon.get(f"/defaults?repo={tmp_repo}")
    assert resolved.status_code == 200, resolved.text
    # The write shape is a strict subset of the read shape: PUT replaces the
    # `defaults` object, while the working-directory catalog comes from a
    # different config section a project commits.
    assert resolved.json() == {**body, "agent_cwds": [], "agent_cwd": None}


def test_project_defaults_report_user_shadowed_fields(
    daemon: TestClient, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    del tmp_state_dir
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(json.dumps({"defaults": {"agent": "claude"}}), encoding="utf-8")
    body = _defaults_body(agent="codex", model="gpt-5.4")

    response = daemon.put(f"/defaults?repo={tmp_repo}&scope=project", json=body)

    assert response.status_code == 200, response.text
    assert response.json()["shadowed"] == ["agent"]
    assert json.loads(paths_mod.project_config_path(tmp_repo).read_text(encoding="utf-8")) == {
        "defaults": {"agent": "codex", "model": "gpt-5.4"}
    }
    resolved = daemon.get(f"/defaults?repo={tmp_repo}")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["agent"] == "claude"
    assert resolved.json()["model"] == "gpt-5.4"


def test_put_defaults_requires_a_complete_replacement(daemon: TestClient, tmp_repo: Path) -> None:
    response = daemon.put(
        f"/defaults?repo={tmp_repo}&scope=project",
        json={"agent": "claude"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"]["error"] == "incomplete_defaults"
    assert "runtime" in response.json()["detail"]["message"]


def test_project_defaults_require_a_repo(daemon: TestClient) -> None:
    response = daemon.put(
        "/defaults?scope=project",
        json=_defaults_body(agent="claude"),
    )

    assert response.status_code == 500, response.text
    assert response.json()["detail"]["error"] == "config_error"
    assert "repository root" in response.json()["detail"]["message"]
