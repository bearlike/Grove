"""GET /agents?repo=<path> — the new-workspace picker's agent list."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path) -> Iterator[TestClient]:
    # `tmp_repo` is DECLARED as a project, because every repo-dispatched route
    # answers only for a root the user registered — a repo with no workspace
    # yet becomes known exactly this way (`grove config add-project`), and
    # without it these reads 404 like `/workspaces` and `/sessions` already do.
    store = JsonWorkspaceStore()
    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})
    app = build_app(cfg=cfg, store=store)
    with TestClient(app) as client:
        yield client


def test_agents_lists_configured_agents(daemon: TestClient, tmp_repo: Path) -> None:
    resp = daemon.get(f"/agents?repo={tmp_repo}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list)
    by_name = {item["name"]: item for item in body}
    # The default cascade ships a claude_code agent plus a generic shell.
    assert "claude" in by_name
    assert by_name["claude"]["kind"] == "claude_code"
    assert "shell" in by_name
    assert by_name["shell"]["kind"] == "generic"


def test_agents_omits_command_and_env(daemon: TestClient, tmp_repo: Path) -> None:
    # command / env can carry host-private paths + secrets — they must not cross
    # the wire. The summary view is name / kind / description / models only.
    resp = daemon.get(f"/agents?repo={tmp_repo}")
    assert resp.status_code == 200, resp.text
    for item in resp.json():
        assert set(item) == {"name", "kind", "description", "models"}


def test_agents_requires_repo(daemon: TestClient) -> None:
    resp = daemon.get("/agents")
    assert resp.status_code == 422


def test_agents_resolves_per_kind_model_catalog(daemon: TestClient, tmp_repo: Path) -> None:
    # The default cascade (claude=claude_code, codex=codex, shell=generic)
    # exercises all three resolve_models branches in one request: the
    # claude_code adapter's stable tier aliases, the codex adapter's fake
    # (conftest-patched) discovery catalog, and generic's empty catalog.
    resp = daemon.get(f"/agents?repo={tmp_repo}")
    assert resp.status_code == 200, resp.text
    by_name = {item["name"]: item for item in resp.json()}
    assert by_name["claude"]["models"] == ["sonnet", "opus", "haiku"]
    assert by_name["codex"]["models"] == ["gpt-5.5", "gpt-5.4"]
    assert by_name["shell"]["models"] == []


def test_a_repo_with_unparseable_config_returns_the_error_envelope(
    tmp_state_dir: Path, fake_tmux: FakeTmux, tmp_repo: Path
) -> None:
    """`registry.get` runs BEFORE each route's own `try/except GroveError`.

    Every per-repo route resolves its manager first, and that resolves the
    repo's config cascade — so an unparseable `.grove/config.json` escaped as a
    bare 500 with no code and no message. One app-level handler covers all eight
    call sites and every route added later, and it applies the same mapping the
    routes already use, so a caught and an uncaught GroveError cannot answer
    differently.
    """
    # Written BEFORE the app starts: the activity service bridges every declared
    # project at startup and caches the Manager it builds, so a config broken
    # afterwards would be answered from that cache and never re-read.
    (tmp_repo / ".grove").mkdir()
    (tmp_repo / ".grove" / "config.json").write_text('{"tmux": {},}\n', encoding="utf-8")
    cfg = daemon_test_config().model_copy(update={"projects": [tmp_repo]})

    with TestClient(build_app(cfg=cfg, store=JsonWorkspaceStore())) as client:
        resp = client.get(f"/agents?repo={tmp_repo}")

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "config_error"
    # The message must name the file and the parse position, since that is the
    # only thing an operator can act on.
    assert ".grove/config.json" in detail["message"]


def test_agents_rejects_an_unregistered_repo(daemon: TestClient, tmp_path: Path) -> None:
    """This route EXECUTES the command the target repo's config names.

    `resolve_models` probes a codex binary by running it, so an unvalidated
    `repo` let any directory on the host — an untrusted checkout, a download —
    choose what a read-only-looking GET runs. Restricting it to roots the user
    registered is the fix; a registered repo naming its own agent command is
    the documented feature.
    """
    hostile = tmp_path / "untrusted"
    (hostile / ".grove").mkdir(parents=True)
    (hostile / ".grove" / "config.json").write_text(
        '{"agents": [{"name": "x", "kind": "codex", "command": "/tmp/attacker-binary"}]}\n',
        encoding="utf-8",
    )
    resp = daemon.get(f"/agents?repo={hostile}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"]["error"] == "unknown_repo_root"
