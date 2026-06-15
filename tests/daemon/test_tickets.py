"""Ticket-provider HTTP routes (#7): provider listing, attach/detach, error mapping.

The deterministic surface (no network) is covered directly: ``GET
/tickets/providers`` is pure config, and attach/detach mutate only
``WorkspaceState.ticket_refs``. The network surface (``/tickets/assigned`` and
``/tickets/{provider}/{id}``) is exercised for its WIRING only — a provider
enabled but missing its token raises ``TicketProviderError`` ("no credential")
→ 502, and a disabled provider name raises ``TicketProviderNotConfigured`` →
404. Both verify the route-to-engine error envelope without a live API.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core import paths
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config

# Gitea enabled (no token) + the other two off. ``gitea.enabled`` with no
# ``GROVE_GITEA_TOKEN`` in the env makes the gitea provider
# ``configured == False`` — the exact half-configured shape the aggregate route
# must skip and the per-provider route must surface as 502. Written as the
# project config (``<repo>/.grove/config.json``) so the daemon's per-repo
# ``load_config`` cascade (the seam ``registry.get`` uses) actually picks it up.
_PROJECT_TICKETS = {
    "tickets": {
        "gitea": {"enabled": True, "owner": "acme", "repo": "widgets"},
    },
}


def _state(ws_id: str, repo_root: str) -> WorkspaceState:
    now = datetime.now(tz=UTC)
    return WorkspaceState(
        id=ws_id,
        title=f"t-{ws_id}",
        repo_root=repo_root,
        branch=f"b-{ws_id}",
        base_branch="main",
        worktree_path=f"{repo_root}/.grove/worktrees/{ws_id}",
        tmux_session=f"grove-{ws_id}",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,  # PAUSED so reconcile is trivial
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def daemon(
    tmp_state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, JsonWorkspaceStore, Path]]:
    # No ticket tokens in the env → enabled providers read as unconfigured.
    monkeypatch.delenv("GROVE_GITEA_TOKEN", raising=False)
    monkeypatch.delenv("GROVE_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GROVE_LINEAR_TOKEN", raising=False)
    repo_root = tmp_state_dir / "repo-a"
    # The daemon resolves each repo's own cascade via ``load_config``; enable
    # gitea in the committed project config so ``registry.get(repo)`` sees it.
    project_cfg = paths.project_config_path(repo_root)
    project_cfg.parent.mkdir(parents=True, exist_ok=True)
    project_cfg.write_text(json.dumps(_PROJECT_TICKETS), encoding="utf-8")
    store = JsonWorkspaceStore()
    store.save(_state("ws1", str(repo_root)))
    # Top-level cfg only supplies auth (disabled); per-repo tickets come from
    # the project config above through the registry's ``load_config`` cascade.
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client, store, repo_root


# ─── GET /tickets/providers (pure config, no network) ───────────────────────


def test_providers_lists_enabled_with_label_and_context(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, repo_root = daemon
    resp = client.get("/tickets/providers", params={"repo": str(repo_root)})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    row = body[0]
    assert row["provider"] == "gitea"
    assert row["label"] == "Gitea"
    assert row["configured"] is False  # enabled but no token in the env
    assert row["context"] == "acme/widgets"


def test_providers_requires_repo_query_param(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, _ = daemon
    resp = client.get("/tickets/providers")
    assert resp.status_code == 422  # missing required `repo` query param


# ─── POST/DELETE attach/detach (pure association, no network) ───────────────


def test_attach_then_detach_roundtrips_ticket_refs(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, _ = daemon
    attach = client.post(
        "/workspaces/ws1/tickets",
        json={"provider": "gitea", "id": "42"},
    )
    assert attach.status_code == 200
    refs = attach.json()["ticket_refs"]
    assert len(refs) == 1
    assert refs[0]["provider"] == "gitea"
    assert refs[0]["id"] == "42"

    detach = client.delete("/workspaces/ws1/tickets/gitea/42")
    assert detach.status_code == 200
    assert detach.json()["ticket_refs"] == []


def test_attach_is_idempotent_by_provider_and_id(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, _ = daemon
    client.post("/workspaces/ws1/tickets", json={"provider": "gitea", "id": "7"})
    again = client.post("/workspaces/ws1/tickets", json={"provider": "gitea", "id": "7"})
    assert again.status_code == 200
    assert len(again.json()["ticket_refs"]) == 1


def test_attach_unknown_workspace_404s(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, _ = daemon
    resp = client.post("/workspaces/nope/tickets", json={"provider": "gitea", "id": "1"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


# ─── error mapping for the network routes (wiring only, no live API) ─────────


def test_assigned_for_enabled_but_tokenless_provider_502s(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, repo_root = daemon
    resp = client.get(
        "/tickets/assigned",
        params={"repo": str(repo_root), "provider": "gitea"},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "ticket_provider_error"


def test_assigned_aggregate_skips_unconfigured_providers(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    # No `provider` → aggregate. The only enabled provider is unconfigured
    # (no token), so it is skipped and the result is an empty 200 rather than
    # a 502 — a half-configured repo must not fail the whole request.
    client, _, repo_root = daemon
    resp = client.get("/tickets/assigned", params={"repo": str(repo_root)})
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_ticket_for_disabled_provider_404s(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    # linear is not enabled in this config → TicketProviderNotConfigured → 404.
    client, _, repo_root = daemon
    resp = client.get("/tickets/linear/ENG-1", params={"repo": str(repo_root)})
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "ticket_provider_not_configured"


def test_get_ticket_for_enabled_but_tokenless_provider_502s(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    client, _, repo_root = daemon
    resp = client.get("/tickets/gitea/42", params={"repo": str(repo_root)})
    assert resp.status_code == 502
    assert resp.json()["detail"]["error"] == "ticket_provider_error"


def test_get_ticket_unknown_provider_name_422s(
    daemon: tuple[TestClient, JsonWorkspaceStore, Path],
) -> None:
    # "bitbucket" isn't in the TicketProviderName literal → path validation 422.
    client, _, repo_root = daemon
    resp = client.get("/tickets/bitbucket/1", params={"repo": str(repo_root)})
    assert resp.status_code == 422
