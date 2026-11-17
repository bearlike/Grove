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


@pytest.mark.launch_builtins
def test_get_defaults_resolves_the_repo_cascade(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """The remote form receives the same selected answers as an in-process create form.

    The files are written before the daemon starts: the view reads the config
    the repo's manager was built with (saved ``defaults`` re-read live), so a
    section other than ``defaults`` edited later reaches neither it nor a
    create until a restart, and the view must say what a create would do.
    """
    del tmp_state_dir, fake_tmux
    user = paths_mod.user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text(
        json.dumps(
            {
                "container": {"enabled": False},
                "brief": {"enabled": False},
                "defaults": {"agent": "claude", "model": "user-model"},
                "runtimes": {
                    "sbx": {
                        "kind": "opensandbox",
                        "image": "grove-runtime:1",
                        "resources": {"cpu": "1"},
                    }
                },
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

    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})
    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as daemon:
        response = daemon.get(f"/defaults?repo={tmp_repo}")

    assert response.status_code == 200, response.text
    # The project's saved agent beats the user's, and the user's model was
    # saved for the user's agent, so the project's agent does not inherit it.
    assert response.json() == {
        "agent": "codex",
        "runtime": "host",
        "brief": False,
        "model": None,
        # Nothing saved a branch mode, so the built-in current checkout.
        "branch_mode": "root",
        "base_ref": "main",
        "skip_init": False,
        # Read-only enrichment: the catalog rides the GET and is deliberately
        # absent from the PUT shape, so a repo declaring none answers empty
        # rather than omitting the keys.
        "agent_cwds": [],
        "agent_cwd": None,
        # This daemon is no hub, so the catalog is itself and its runtimes.
        "targets": [{"node": None, "runtimes": ["sbx"]}],
        "sources": {
            "agent_name": "project",
            "runtime": "builtin",
            "branch_plan": "builtin",
            "model": "builtin",
            "brief": "config",
            "skip_init": "builtin",
            "project_cwd": "builtin",
        },
        "problems": [],
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
    # `existing` needs a branch name a saved default does not hold, so the
    # view reports the problem rather than pretending it maps to a new branch.
    assert resolved.json() == {
        **body,
        "agent_cwds": [],
        "agent_cwd": None,
        "targets": [{"node": None, "runtimes": []}],
        "sources": {
            "agent_name": "project",
            "runtime": "project",
            "branch_plan": "project",
            "model": "project",
            "brief": "project",
            "skip_init": "project",
            "project_cwd": "builtin",
        },
        "problems": [
            {
                "field": "branch_plan",
                "error": "incomplete_default",
                "message": (
                    "the saved branch mode 'existing' needs a branch name, which a saved "
                    "default does not hold; choose the branch for this create"
                ),
            }
        ],
    }


def test_user_defaults_report_project_shadowed_fields(
    daemon: TestClient, tmp_state_dir: Path, tmp_repo: Path
) -> None:
    """A user save under a project that saved the same field cannot take effect there."""
    del tmp_state_dir
    project = paths_mod.project_config_path(tmp_repo)
    project.parent.mkdir(parents=True, exist_ok=True)
    project.write_text(json.dumps({"defaults": {"agent": "claude"}}), encoding="utf-8")
    body = _defaults_body(agent="codex", model="gpt-5.4")

    response = daemon.put(f"/defaults?repo={tmp_repo}&scope=user", json=body)

    assert response.status_code == 200, response.text
    assert response.json()["shadowed"] == ["agent"]
    resolved = daemon.get(f"/defaults?repo={tmp_repo}")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["agent"] == "claude"
    assert resolved.json()["sources"]["agent_name"] == "project"


def test_user_defaults_report_nothing_shadowed_without_a_repo(daemon: TestClient) -> None:
    response = daemon.put("/defaults?scope=user", json=_defaults_body(agent="codex"))

    assert response.status_code == 200, response.text
    assert response.json()["shadowed"] == []


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


# ─── the product built-ins, end to end through POST /workspaces ─────────────


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.launch_builtins
def test_an_omitted_create_runs_on_the_host_in_the_current_checkout(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """Nothing saved, agent and plan omitted: host and root recorded, as GET /defaults said."""
    shown = daemon.get(f"/defaults?repo={tmp_repo}").json()

    created = daemon.post("/workspaces", json={"repo_root": str(tmp_repo)})

    assert created.status_code == 200, created.text
    state = created.json()
    assert (state["runtime"], state["placement"]) == ("host", "root")
    assert state["worktree_path"] == str(tmp_repo)
    assert (shown["agent"], shown["runtime"], shown["branch_mode"]) == (
        state["agent_name"],
        state["runtime"],
        state["placement"],
    )


@pytest.mark.launch_builtins
def test_a_project_runtime_default_beats_the_users_and_the_view_agrees(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """Project container over user host; with docker unavailable the saved default
    falls back to the host and says so, exactly as the view's source names.

    The config is written before the daemon starts: a daemon caches each repo's
    cascade with its Manager on first access, by design.
    """
    del tmp_state_dir, fake_tmux
    _write(paths_mod.user_config_path(), {"defaults": {"runtime": "host"}})
    _write(paths_mod.project_config_path(tmp_repo), {"defaults": {"runtime": "container"}})
    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})
    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as daemon:
        shown = daemon.get(f"/defaults?repo={tmp_repo}").json()
        created = daemon.post("/workspaces", json={"repo_root": str(tmp_repo)})

    assert shown["runtime"] == "container"
    assert shown["sources"]["runtime"] == "project"
    assert created.status_code == 200, created.text
    # Arm 4: a saved container default degrades loudly rather than refusing.
    assert created.json()["runtime"] == "host"
    assert created.json()["runtime_fallback_reason"]


@pytest.mark.launch_builtins
def test_an_explicit_container_with_docker_unavailable_is_refused_before_side_effects(
    daemon: TestClient, tmp_repo: Path
) -> None:
    refused = daemon.post("/workspaces", json={"repo_root": str(tmp_repo), "runtime": "container"})

    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert (detail["error"], detail["field"]) == ("runtime_unavailable", "runtime")
    assert daemon.get(f"/workspaces?repo={tmp_repo}").json() == []
