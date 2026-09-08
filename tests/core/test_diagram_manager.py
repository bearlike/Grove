"""WorkspaceManager diagram descriptor lifecycle and fencing."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.diagrams import (
    DiagramOpenRequest,
    DiagramPreviewUploadRequest,
    DiagramStopRequest,
    DiagramUpdateRequest,
)
from grove.core.errors import DiagramConflict, DiagramUnavailable
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Placement, WorkspaceState, WorkspaceStatus

_VALID_XML = (
    '<mxfile><diagram id="page"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/>'
    "</root></mxGraphModel></diagram></mxfile>"
)


def _updated_xml(page: str) -> str:
    return (
        f'<mxfile><diagram id="{page}"><mxGraphModel><root>'
        '<mxCell id="0"/><mxCell id="1" parent="0"/>'
        "</root></mxGraphModel></diagram></mxfile>"
    )


@pytest.fixture
def manager(tmp_path: Path) -> tuple[WorkspaceManager, WorkspaceState, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = repo / "worktree"
    worktree.mkdir()
    diagram = worktree / "architecture.drawio"
    diagram.write_text(_VALID_XML)
    state = WorkspaceState(
        id="a" * 32,
        title="architecture",
        repo_root=str(repo),
        branch="main",
        base_branch="main",
        worktree_path=str(worktree),
        tmux_session="architecture",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(state)
    return WorkspaceManager(repo_root=repo, cfg=GroveConfig(), store=store), state, diagram


def test_open_is_idempotent_and_persists_active_descriptor(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, _diagram = manager
    request = DiagramOpenRequest(path="architecture.drawio")
    first = mgr.open_diagram(state.id, request)
    second = mgr.open_diagram(state.id, request)

    assert first.diagram.mode == "active"
    assert first.diagram.session_id == second.diagram.session_id
    assert mgr.store.get(state.id).diagram == first.diagram


def test_preview_is_first_page_png_and_revision_fenced(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, _diagram = manager
    opened = mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    png = b"\x89PNG\r\n\x1a\npreview"
    saved = mgr.save_diagram_preview(
        state.id,
        DiagramPreviewUploadRequest(
            session_id=opened.diagram.session_id,
            expected_revision=opened.revision,
            content_base64=base64.b64encode(png).decode(),
        ),
    )
    read = mgr.read_diagram_preview(state.id)

    assert saved.page_index == read.page_index == 0
    assert read.revision == opened.revision
    assert base64.b64decode(read.content_base64) == png
    assert read.attachment.path.endswith("diagram-preview.png")

    _diagram.write_text(_updated_xml("changed"))
    with pytest.raises(DiagramUnavailable, match="pending"):
        mgr.read_diagram_preview(state.id)


def test_preview_rejects_non_png_before_it_is_published(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, _diagram = manager
    opened = mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))

    with pytest.raises(Exception, match="PNG"):
        mgr.save_diagram_preview(
            state.id,
            DiagramPreviewUploadRequest(
                session_id=opened.diagram.session_id,
                expected_revision=opened.revision,
                content_base64=base64.b64encode(b"not an image").decode(),
            ),
        )


def test_active_other_path_conflicts(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, diagram = manager
    (diagram.parent / "other.drawio").write_text(_updated_xml("other"))
    mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    with pytest.raises(DiagramConflict):
        mgr.open_diagram(state.id, DiagramOpenRequest(path="other.drawio"))


def test_update_fences_stale_revision_and_external_write(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, diagram = manager
    opened = mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    saved = mgr.update_diagram(
        state.id,
        DiagramUpdateRequest(
            session_id=opened.diagram.session_id,
            expected_revision=opened.revision,
            xml=_updated_xml("saved"),
        ),
    )
    with pytest.raises(DiagramConflict):
        mgr.update_diagram(
            state.id,
            DiagramUpdateRequest(
                session_id=opened.diagram.session_id,
                expected_revision=opened.revision,
                xml=_updated_xml("stale"),
            ),
        )
    diagram.write_text(_updated_xml("external"))
    with pytest.raises(DiagramConflict):
        mgr.update_diagram(
            state.id,
            DiagramUpdateRequest(
                session_id=opened.diagram.session_id,
                expected_revision=saved.revision,
                xml=_updated_xml("stale"),
            ),
        )


def test_concurrent_writes_at_one_revision_admit_only_one(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, _diagram = manager
    opened = mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    barrier = Barrier(2)

    def save(page: str) -> str:
        barrier.wait()
        try:
            return mgr.update_diagram(
                state.id,
                DiagramUpdateRequest(
                    session_id=opened.diagram.session_id,
                    expected_revision=opened.revision,
                    xml=_updated_xml(page),
                ),
            ).revision
        except DiagramConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ("left", "right")))

    assert results.count("conflict") == 1
    assert len({result for result in results if result != "conflict"}) == 1


def test_root_workspaces_sharing_one_file_serialize_writes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    diagram = repo / "architecture.drawio"
    diagram.write_text(_VALID_XML)
    now = datetime.now(tz=UTC)
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    first = WorkspaceState(
        id="a" * 32,
        title="first",
        repo_root=str(repo),
        branch="main",
        base_branch="main",
        worktree_path=str(repo),
        tmux_session="first",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        placement=Placement.ROOT,
    )
    second = WorkspaceState(
        id="b" * 32,
        title="second",
        repo_root=str(repo),
        branch="main",
        base_branch="main",
        worktree_path=str(repo),
        tmux_session="second",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        placement=Placement.ROOT,
    )
    store.save(first)
    store.save(second)
    first_manager = WorkspaceManager(repo_root=repo, cfg=GroveConfig(), store=store)
    second_manager = WorkspaceManager(repo_root=repo, cfg=GroveConfig(), store=store)
    request = DiagramOpenRequest(path="architecture.drawio")
    first_opened = first_manager.open_diagram(first.id, request)
    second_opened = second_manager.open_diagram(second.id, request)
    barrier = Barrier(2)

    def save(manager: WorkspaceManager, workspace_id: str, session_id: str, page: str) -> str:
        barrier.wait()
        try:
            return manager.update_diagram(
                workspace_id,
                DiagramUpdateRequest(
                    session_id=session_id,
                    expected_revision=first_opened.revision,
                    xml=_updated_xml(page),
                ),
            ).revision
        except DiagramConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda args: save(*args),
                (
                    (first_manager, first.id, first_opened.diagram.session_id, "first"),
                    (second_manager, second.id, second_opened.diagram.session_id, "second"),
                ),
            )
        )

    assert results.count("conflict") == 1
    assert len({result for result in results if result != "conflict"}) == 1


def test_opening_two_paths_in_one_workspace_refuses_second_active_path(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, diagram = manager
    (diagram.parent / "second.drawio").write_text(_updated_xml("second"))
    mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))

    with pytest.raises(DiagramConflict):
        mgr.open_diagram(state.id, DiagramOpenRequest(path="second.drawio"))


def test_stop_then_reopen_fences_late_saves_and_preserves_read_only(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, _diagram = manager
    opened = mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    stopped = mgr.stop_diagram(
        state.id,
        DiagramStopRequest(session_id=opened.diagram.session_id, expected_revision=opened.revision),
    )
    assert stopped.diagram.mode == "read_only"
    assert mgr.read_diagram(state.id).diagram.mode == "read_only"
    with pytest.raises(DiagramConflict):
        mgr.update_diagram(
            state.id,
            DiagramUpdateRequest(
                session_id=opened.diagram.session_id,
                expected_revision=stopped.revision,
                xml=_updated_xml("stale"),
            ),
        )

    reopened = mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    assert reopened.diagram.session_id != opened.diagram.session_id
    with pytest.raises(DiagramConflict):
        mgr.update_diagram(
            state.id,
            DiagramUpdateRequest(
                session_id=opened.diagram.session_id,
                expected_revision=reopened.revision,
                xml=_updated_xml("stale"),
            ),
        )


def test_read_without_descriptor_and_missing_file_are_unavailable(
    manager: tuple[WorkspaceManager, WorkspaceState, Path],
) -> None:
    mgr, state, diagram = manager
    with pytest.raises(DiagramUnavailable):
        mgr.read_diagram(state.id)
    mgr.open_diagram(state.id, DiagramOpenRequest(path="architecture.drawio"))
    diagram.unlink()
    with pytest.raises(DiagramUnavailable):
        mgr.read_diagram(state.id)
