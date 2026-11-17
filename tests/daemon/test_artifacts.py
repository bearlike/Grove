"""Artifact HTTP routes: attach, the content read and its headers, detach, error mapping.

The engine's validation is covered in ``tests/core/test_artifacts.py``; this
pins the wiring. The workspace is PAUSED so reconcile is trivial, and files
live under the repo root, which is always an allowed directory. "Outside" is
``/etc/hosts``: it exists on every CI host and is under neither the repo nor
the default ``/tmp`` root.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config
from tests.daemon.test_tickets import _state


@pytest.fixture
def daemon(tmp_state_dir: Path) -> Iterator[tuple[TestClient, Path]]:
    repo_root = tmp_state_dir / "repo-a"
    repo_root.mkdir()
    store = JsonWorkspaceStore()
    store.save(_state("ws1", str(repo_root)))
    with TestClient(build_app(cfg=daemon_test_config(), store=store)) as client:
        yield client, repo_root


def test_attach_read_and_detach_a_file(daemon: tuple[TestClient, Path]) -> None:
    client, repo_root = daemon
    page = repo_root / "report.html"
    page.write_text("<script>alert(1)</script>", encoding="utf-8")

    attached = client.post(
        "/workspaces/ws1/artifacts", json={"target": str(page), "title": "Report"}
    )
    assert attached.status_code == 200, attached.text
    (ref,) = attached.json()["artifact_refs"]
    assert (ref["kind"], ref["target"], ref["title"]) == ("file", str(page), "Report")

    content = client.get(f"/workspaces/ws1/artifacts/{ref['id']}/content")
    assert content.status_code == 200
    assert content.content == b"<script>alert(1)</script>"
    # An agent-written HTML file must download, never render on this origin.
    assert content.headers["content-type"] == "application/octet-stream"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert "report.html" in content.headers["content-disposition"]

    detached = client.delete("/workspaces/ws1/artifacts", params={"ref": str(page)})
    assert detached.json()["artifact_refs"] == []
    gone = client.get(f"/workspaces/ws1/artifacts/{ref['id']}/content")
    assert gone.status_code == 404
    assert gone.json()["detail"]["error"] == "artifact_unavailable"


def test_a_link_attaches_and_has_no_content(daemon: tuple[TestClient, Path]) -> None:
    client, _repo_root = daemon
    attached = client.post("/workspaces/ws1/artifacts", json={"target": "https://example.com/d"})
    (ref,) = attached.json()["artifact_refs"]
    assert ref["kind"] == "link"
    refused = client.get(f"/workspaces/ws1/artifacts/{ref['id']}/content")
    assert refused.status_code == 422


@pytest.mark.parametrize("target", ["/etc/hosts", "javascript://x", "missing.md"])
def test_a_refused_target_is_a_typed_422(daemon: tuple[TestClient, Path], target: str) -> None:
    client, _repo_root = daemon
    refused = client.post("/workspaces/ws1/artifacts", json={"target": target})
    assert refused.status_code == 422
    assert refused.json()["detail"]["error"] == "artifact_invalid"


def test_an_unknown_workspace_is_404(daemon: tuple[TestClient, Path]) -> None:
    client, _repo_root = daemon
    assert (
        client.post("/workspaces/nope/artifacts", json={"target": "https://e.com"}).status_code
        == 404
    )
