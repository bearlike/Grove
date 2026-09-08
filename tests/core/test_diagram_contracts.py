"""The diagram contract fences paths and versions before any file I/O."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core.contracts import (
    DiagramOpenRequest,
    DiagramSessionView,
    DiagramUpdateRequest,
    WorkspaceStateView,
)
from grove.core.contracts.public import PublicWorkspaceStateView
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/a.drawio",
        "../a.drawio",
        "a/../b.drawio",
        "a//b.drawio",
        "./a.drawio",
        "C:/a.drawio",
        "a\\b.drawio",
        "a.xml",
        "a.drawio\n",
        "~/a.drawio",
    ],
)
def test_diagram_path_rejects_unsafe_names(path: str) -> None:
    with pytest.raises(ValidationError):
        DiagramOpenRequest(path=path)


def test_diagram_contract_keeps_relative_unicode_paths() -> None:
    assert DiagramOpenRequest(path="design/流程.drawio").path == "design/流程.drawio"


def test_update_requires_both_revision_and_collaboration_identity() -> None:
    with pytest.raises(ValidationError):
        DiagramUpdateRequest(xml="<mxfile/>", expected_revision="a" * 64)
    with pytest.raises(ValidationError):
        DiagramUpdateRequest(xml="<mxfile/>", expected_revision="old", session_id="b" * 32)


def test_public_workspace_never_inherits_diagram_descriptor() -> None:
    assert "diagram" in WorkspaceStateView.model_fields
    assert "diagram" not in PublicWorkspaceStateView.model_fields


@pytest.mark.parametrize("mode", ["active", "read_only"])
def test_diagram_descriptor_survives_store_reload(tmp_path: Path, mode: str) -> None:
    now = datetime.now(UTC)
    descriptor = DiagramSessionView(path="design.drawio", session_id="a" * 32, mode=mode)
    state = WorkspaceState(
        id="diagram-workspace",
        title="Diagram",
        repo_root=str(tmp_path),
        branch="feature/diagram",
        base_branch="main",
        worktree_path=str(tmp_path),
        tmux_session="diagram",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        diagram=descriptor,
    )
    path = tmp_path / "state.json"
    JsonWorkspaceStore(path=path).save(state)
    loaded = JsonWorkspaceStore(path=path).get(state.id)
    assert loaded.diagram == descriptor
    assert WorkspaceStateView.from_state(loaded).diagram == descriptor
    serialized = json.loads(path.read_text())
    assert serialized["workspaces"][state.id]["diagram"] == descriptor.model_dump()
    del serialized["workspaces"][state.id]["diagram"]
    path.write_text(json.dumps(serialized))
    assert JsonWorkspaceStore(path=path).get(state.id).diagram is None
