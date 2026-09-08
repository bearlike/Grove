"""Diagram routes preserve the authenticated revisioned manager boundary.

Core owns file validation, XML semantics, and descriptor transitions. These
route tests pin normal auth, the frozen HTTP verbs, typed errors, and that
mutations use the daemon lifecycle runner rather than direct loop-thread calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from grove.core.config import GroveConfig
from grove.core.contracts.diagrams import DiagramDocumentView, DiagramPreviewView
from grove.core.errors import DiagramConflict, DiagramUnavailable
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from grove.daemon._lifecycle import _LifecycleRunner
from tests.daemon.conftest import daemon_test_config

_SESSION_ID = "a" * 32
_REVISION = "b" * 64
_XML = (
    '<mxfile><diagram id="page" name="Page 1"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    "</root></mxGraphModel></diagram></mxfile>"
)


def _document(mode: str = "active") -> DiagramDocumentView:
    return DiagramDocumentView.model_validate(
        {
            "diagram": {"path": "design.drawio", "session_id": _SESSION_ID, "mode": mode},
            "revision": _REVISION,
            "xml": _XML,
        }
    )


def _preview() -> DiagramPreviewView:
    return DiagramPreviewView.model_validate(
        {
            "session_id": _SESSION_ID,
            "revision": _REVISION,
            "page_index": 0,
            "attachment": {
                "id": "a" * 12,
                "name": "diagram-preview.png",
                "path": "/preview.png",
            },
            "mime_type": "image/png",
            "content_base64": "iVBORw0KGgo=",
        }
    )


def _state(repo_root: Path) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id="ws1",
        title="diagram",
        repo_root=str(repo_root),
        branch="main",
        base_branch="main",
        worktree_path=str(repo_root / ".grove" / "worktrees" / "diagram"),
        tmux_session="grove-diagram",
        agent_name="claude",
        status=WorkspaceStatus.PAUSED,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def daemon(tmp_state_dir: Path) -> tuple[TestClient, Path]:
    repo_root = tmp_state_dir / "repo"
    repo_root.mkdir()
    store = JsonWorkspaceStore()
    store.save(_state(repo_root))
    app = build_app(cfg=daemon_test_config(), store=store)
    client = TestClient(app)
    client.__enter__()
    try:
        yield client, repo_root
    finally:
        client.__exit__(None, None, None)


def test_diagram_route_requires_normal_bearer_before_manager_resolution(
    tmp_state_dir: Path,
) -> None:
    """Diagram XML is never added to the public namespace or an auth bypass."""
    store = JsonWorkspaceStore()
    app = build_app(cfg=GroveConfig.model_validate({"auth": {"enabled": True}}), store=store)
    with TestClient(app) as client:
        response = client.get(
            "/workspaces/ws1/diagram", params={"repo": str(tmp_state_dir / "repo")}
        )
    assert response.status_code == 401


def test_diagram_mutations_use_frozen_routes_and_lifecycle_runner(
    daemon: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = daemon
    calls: list[tuple[str, tuple[Any, ...]]] = []
    lifecycle_calls: list[tuple[str | None, str]] = []

    def open_diagram(
        self: WorkspaceManager, workspace_id: str, request: Any
    ) -> DiagramDocumentView:
        calls.append(("open", (workspace_id, request)))
        return _document()

    def update_diagram(
        self: WorkspaceManager, workspace_id: str, request: Any
    ) -> DiagramDocumentView:
        calls.append(("update", (workspace_id, request)))
        return _document()

    def stop_diagram(
        self: WorkspaceManager, workspace_id: str, request: Any
    ) -> DiagramDocumentView:
        calls.append(("stop", (workspace_id, request)))
        return _document(mode="read_only")

    original_run = _LifecycleRunner.run

    async def recording_run(self: _LifecycleRunner, key: str | None, fn: Any, *args: Any) -> Any:
        lifecycle_calls.append((key, fn.__name__))
        return await original_run(self, key, fn, *args)

    monkeypatch.setattr(WorkspaceManager, "open_diagram", open_diagram)
    monkeypatch.setattr(WorkspaceManager, "update_diagram", update_diagram)
    monkeypatch.setattr(WorkspaceManager, "stop_diagram", stop_diagram)
    monkeypatch.setattr(_LifecycleRunner, "run", recording_run)

    opened = client.post(
        "/workspaces/ws1/diagram",
        json={"path": "design.drawio"},
    )
    updated = client.put(
        "/workspaces/ws1/diagram",
        json={"session_id": _SESSION_ID, "expected_revision": _REVISION, "xml": _XML},
    )
    stopped = client.post(
        "/workspaces/ws1/diagram/stop",
        json={"session_id": _SESSION_ID, "expected_revision": _REVISION},
    )

    assert opened.status_code == updated.status_code == stopped.status_code == 200
    assert stopped.json()["diagram"]["mode"] == "read_only"
    assert lifecycle_calls == [
        ("ws1", "open_diagram"),
        ("ws1", "update_diagram"),
        ("ws1", "stop_diagram"),
    ]
    assert calls[0][1][1].path == "design.drawio"
    assert calls[1][1][1].expected_revision == _REVISION
    assert calls[2][1][1].session_id == _SESSION_ID


def test_preview_routes_use_authenticated_get_and_serialized_post(
    daemon: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = daemon
    calls: list[tuple[str, str]] = []

    def save_preview(self: WorkspaceManager, workspace_id: str, request: Any) -> DiagramPreviewView:
        calls.append(("save", workspace_id))
        assert request.expected_revision == _REVISION
        return _preview()

    def read_preview(self: WorkspaceManager, workspace_id: str) -> DiagramPreviewView:
        calls.append(("read", workspace_id))
        return _preview()

    monkeypatch.setattr(WorkspaceManager, "save_diagram_preview", save_preview)
    monkeypatch.setattr(WorkspaceManager, "read_diagram_preview", read_preview)

    uploaded = client.post(
        "/workspaces/ws1/diagram/preview",
        json={
            "session_id": _SESSION_ID,
            "expected_revision": _REVISION,
            "content_base64": "iVBORw0KGgo=",
        },
    )
    read = client.get("/workspaces/ws1/diagram/preview")

    assert uploaded.status_code == read.status_code == 200
    assert read.json()["page_index"] == 0
    assert calls == [("save", "ws1"), ("read", "ws1")]


def test_diagram_read_is_a_regular_off_loop_manager_read(
    daemon: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = daemon
    calls: list[str] = []

    def read_diagram(self: WorkspaceManager, workspace_id: str) -> DiagramDocumentView:
        calls.append(workspace_id)
        return _document(mode="read_only")

    monkeypatch.setattr(WorkspaceManager, "read_diagram", read_diagram)
    response = client.get("/workspaces/ws1/diagram")

    assert response.status_code == 200
    assert response.json()["diagram"]["mode"] == "read_only"
    assert calls == ["ws1"]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (DiagramConflict("stale diagram revision"), 409, "diagram_conflict"),
        (DiagramUnavailable("diagram file is absent"), 404, "diagram_unavailable"),
    ],
)
def test_diagram_errors_keep_the_typed_http_envelope(
    daemon: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    code: str,
) -> None:
    client, _ = daemon

    def open_diagram(
        self: WorkspaceManager, workspace_id: str, request: Any
    ) -> DiagramDocumentView:
        del self, workspace_id, request
        raise error

    monkeypatch.setattr(WorkspaceManager, "open_diagram", open_diagram)
    response = client.post("/workspaces/ws1/diagram", json={"path": "design.drawio"})

    assert response.status_code == status
    assert response.json()["detail"]["error"] == code


def test_real_diagram_routes_persist_and_fence_writes(tmp_state_dir: Path) -> None:
    """Exercise HTTP → manager → real file, not a manager-shaped route double."""
    repo = tmp_state_dir / "repo"
    repo.mkdir()
    state = _state(repo)
    worktree = Path(state.worktree_path)
    worktree.mkdir(parents=True)
    source = worktree / "design.drawio"
    source.write_text(_XML)
    store = JsonWorkspaceStore()
    store.save(state)
    app = build_app(cfg=daemon_test_config(), store=store)
    route = f"/workspaces/{state.id}/diagram"
    with TestClient(app) as client:
        opened = client.post(route, json={"path": "design.drawio"})
        assert opened.status_code == 200, opened.text
        document = opened.json()
        update = {
            "session_id": document["diagram"]["session_id"],
            "expected_revision": document["revision"],
            "xml": _XML.replace('name="Page 1"', 'name="Human edit"'),
        }
        saved = client.put(route, json=update)
        assert saved.status_code == 200, saved.text
        assert 'name="Human edit"' in source.read_text()
        assert client.put(route, json=update).status_code == 409
        current = saved.json()
        stopped = client.post(
            route + "/stop",
            json={
                "session_id": current["diagram"]["session_id"],
                "expected_revision": current["revision"],
            },
        )
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()["diagram"]["mode"] == "read_only"
        assert (
            client.put(route, json={**update, "expected_revision": current["revision"]}).status_code
            == 409
        )
        assert client.get(route).json()["xml"] == source.read_text()
        reopened = client.post(route, json={"path": "design.drawio"}).json()
        assert reopened["diagram"]["session_id"] != current["diagram"]["session_id"]
        assert (
            client.put(
                route, json={**update, "expected_revision": reopened["revision"]}
            ).status_code
            == 409
        )
