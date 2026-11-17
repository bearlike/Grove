"""POST /launch/preview over HTTP, and the readiness memo that keeps it cheap."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from grove.daemon._readiness import _ReadinessMemo
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config

pytestmark = pytest.mark.launch_builtins


@pytest.fixture
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path) -> Iterator[TestClient]:
    del tmp_state_dir, fake_tmux
    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})
    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as client:
        yield client


def test_the_preview_names_what_the_create_then_records(daemon: TestClient, tmp_repo: Path) -> None:
    body = {"repo_root": str(tmp_repo), "title": "same"}

    preview = daemon.post("/launch/preview", json=body)
    created = daemon.post("/workspaces", json=body)

    assert preview.status_code == 200, preview.text
    assert created.status_code == 200, created.text
    shown, state = preview.json(), created.json()
    assert shown["problems"] == []
    assert (shown["agent_name"], shown["runtime"], shown["placement"]) == (
        state["agent_name"],
        state["runtime"],
        state["placement"],
    )
    assert shown["sources"]["branch_plan"] == "builtin"


def test_a_refusal_is_a_200_problem_named_by_field_and_the_create_refuses_alike(
    daemon: TestClient, tmp_repo: Path
) -> None:
    body = {"repo_root": str(tmp_repo), "runtime": "container"}

    preview = daemon.post("/launch/preview", json=body)
    refused = daemon.post("/workspaces", json=body)

    assert preview.status_code == 200, preview.text
    assert [(p["field"], p["error"]) for p in preview.json()["problems"]] == [
        ("runtime", "runtime_unavailable")
    ]
    assert refused.status_code == 422, refused.text
    assert (refused.json()["detail"]["field"], refused.json()["detail"]["error"]) == (
        "runtime",
        "runtime_unavailable",
    )


def test_the_preview_refuses_an_unregistered_repo(daemon: TestClient, tmp_path: Path) -> None:
    """`repo_root` selects configuration, so it is gated like every other repo route."""
    stranger = tmp_path / "stranger"
    stranger.mkdir()

    response = daemon.post("/launch/preview", json={"repo_root": str(stranger)})

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"] == "unknown_repo_root"


def test_a_saved_default_shows_in_get_defaults_and_preview_at_once(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """GET /defaults reads the manager's config; a PUT must still show without a restart."""
    daemon.post("/launch/preview", json={"repo_root": str(tmp_repo)})  # mint the manager
    body = {
        "agent": None,
        "runtime": None,
        "brief": None,
        "model": None,
        "branch_mode": "auto",
        "base_ref": None,
        "skip_init": None,
    }

    saved = daemon.put(f"/defaults?repo={tmp_repo}&scope=project", json=body)
    shown = daemon.get(f"/defaults?repo={tmp_repo}").json()
    preview = daemon.post("/launch/preview", json={"repo_root": str(tmp_repo)}).json()

    assert saved.status_code == 200, saved.text
    assert (shown["branch_mode"], shown["sources"]["branch_plan"]) == ("auto", "project")
    assert (preview["placement"], preview["sources"]["branch_plan"]) == ("worktree", "project")


async def test_the_readiness_memo_probes_once_for_a_burst_and_again_when_stale() -> None:
    now = [0.0]
    memo = _ReadinessMemo(clock=lambda: now[0])
    calls: list[int] = []
    release = threading.Event()

    def probe() -> str | None:
        calls.append(1)
        release.wait(5)
        return "docker is down"

    root = Path("/repo")
    burst = [asyncio.ensure_future(memo.reason(root, probe)) for _ in range(5)]
    await asyncio.sleep(0.05)
    release.set()
    assert await asyncio.gather(*burst) == ["docker is down"] * 5
    assert len(calls) == 1

    assert await memo.reason(root, probe) == "docker is down"
    assert len(calls) == 1
    now[0] = _ReadinessMemo.TTL_SECONDS + 1
    await memo.reason(root, probe)
    assert len(calls) == 2


async def test_a_cancelled_first_caller_still_stores_the_reading() -> None:
    """A closed tab does not stop the probe thread, so its answer is still kept."""
    memo = _ReadinessMemo(clock=lambda: 0.0)
    release = threading.Event()
    calls: list[int] = []

    def probe() -> str | None:
        calls.append(1)
        release.wait(5)
        return None

    root = Path("/repo")
    first = asyncio.ensure_future(memo.reason(root, probe))
    await asyncio.sleep(0.05)
    first.cancel()
    release.set()
    await asyncio.sleep(0.1)

    assert await memo.reason(root, probe) is None
    assert len(calls) == 1


def test_launch_options_lists_this_daemons_runtimes_and_unset_drivers(
    daemon: TestClient, tmp_repo: Path
) -> None:
    response = daemon.get(f"/launch/options?repo={tmp_repo}")

    assert response.status_code == 200, response.text
    rows = {row["id"]: row for row in response.json()["runtimes"]}
    assert rows["host"]["kind"] == "host"
    # The suite's offline fixture makes docker unreachable, so the container
    # row reports a measured refusal rather than a fabricated ready.
    assert rows["container"]["status"] == "unavailable"
    assert rows["container"]["reason"]
    assert rows["driver:opensandbox"]["status"] == "not_configured"
    assert all(row["node"] is None for row in rows.values())


def test_launch_options_refuses_an_unregistered_repo(daemon: TestClient, tmp_path: Path) -> None:
    stranger = tmp_path / "stranger"
    stranger.mkdir()

    response = daemon.get(f"/launch/options?repo={stranger}")

    assert response.status_code == 404, response.text
    assert response.json()["detail"]["error"] == "unknown_repo_root"


def test_agents_and_workspaces_carry_the_new_launch_facts(
    daemon: TestClient, tmp_repo: Path
) -> None:
    agents = {a["name"]: a for a in daemon.get(f"/agents?repo={tmp_repo}").json()}
    created = daemon.post("/workspaces", json={"repo_root": str(tmp_repo)}).json()

    assert agents["claude"]["launch_modes"] == ["native", "terminal"]
    assert "remote" in agents["claude"]["runtimes"]
    assert agents["shell"]["launch_modes"] == ["terminal"]
    assert (created["project_subpath"], created["remote_runtime"]) == ("", None)


def test_an_out_of_repo_cwd_is_one_422_with_its_field_in_preview_and_create(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """It used to escape create as a 500 grove_error with no field, unlike the preview."""
    body = {"repo_root": str(tmp_repo), "project_cwd": "/elsewhere"}

    preview = daemon.post("/launch/preview", json=body).json()
    refused = daemon.post("/workspaces", json=body)

    assert refused.status_code == 422, refused.text
    detail = refused.json()["detail"]
    assert (detail["error"], detail["field"]) == ("project_cwd_outside_repo", "project_cwd")
    assert [(p["error"], p["field"]) for p in preview["problems"]] == [
        (detail["error"], detail["field"])
    ]
