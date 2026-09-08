"""The ``grove diagram`` CLI keeps its revision protocol in the manager."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from grove.core import GroveError
from grove.core.contracts.diagrams import DiagramDocumentView
from grove.core.manager import WorkspaceManager
from grove.tui.cli import app
from grove.tui.cli_diagram import _MAX_XML_BYTES, _validate_input
from tests.conftest import FakeTmux

_SESSION_ID = "a" * 32
_REVISION = "b" * 64
_XML = '<mxfile host="app.diagrams.net"><diagram id="page">x</diagram></mxfile>'


def _document(mode: str = "active") -> DiagramDocumentView:
    return DiagramDocumentView.model_validate(
        {
            "diagram": {"path": "design.drawio", "session_id": _SESSION_ID, "mode": mode},
            "revision": _REVISION,
            "xml": _XML,
        }
    )


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_state_dir: Path,
    tmp_repo: Path,
    fake_tmux: FakeTmux,
) -> Path:
    del tmp_state_dir, fake_tmux
    monkeypatch.chdir(tmp_repo)
    return tmp_repo


def _create(runner: CliRunner) -> str:
    created = runner.invoke(app, ["create", "diagram", "--agent", "claude"])
    assert created.exit_code == 0, created.output
    for line in created.output.splitlines():
        if "created " in line:
            return line.split("created ", 1)[1].strip()
    raise AssertionError(created.output)


def test_diagram_commands_forward_only_typed_requests_and_emit_json(
    runner: CliRunner, project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del project
    workspace_id = _create(runner)
    calls: list[tuple[str, Any]] = []
    input_xml = tmp_path / "update.drawio"
    input_xml.write_text(_XML, encoding="utf-8")

    def open_diagram(self: WorkspaceManager, ws_id: str, request: Any) -> DiagramDocumentView:
        del self
        calls.append(("open", (ws_id, request)))
        return _document()

    def read_diagram(self: WorkspaceManager, ws_id: str) -> DiagramDocumentView:
        del self
        calls.append(("read", ws_id))
        return _document()

    def update_diagram(self: WorkspaceManager, ws_id: str, request: Any) -> DiagramDocumentView:
        del self
        calls.append(("update", (ws_id, request)))
        return _document()

    def stop_diagram(self: WorkspaceManager, ws_id: str, request: Any) -> DiagramDocumentView:
        del self
        calls.append(("stop", (ws_id, request)))
        return _document(mode="read_only")

    monkeypatch.setattr(WorkspaceManager, "open_diagram", open_diagram)
    monkeypatch.setattr(WorkspaceManager, "read_diagram", read_diagram)
    monkeypatch.setattr(WorkspaceManager, "update_diagram", update_diagram)
    monkeypatch.setattr(WorkspaceManager, "stop_diagram", stop_diagram)

    opened = runner.invoke(app, ["diagram", "open", "design.drawio", "-w", workspace_id])
    read = runner.invoke(app, ["diagram", "read", "-w", workspace_id])
    updated = runner.invoke(
        app,
        [
            "diagram",
            "update",
            str(input_xml),
            "--revision",
            _REVISION,
            "--session-id",
            _SESSION_ID,
            "-w",
            workspace_id,
        ],
    )
    stopped = runner.invoke(
        app,
        [
            "diagram",
            "stop",
            "--revision",
            _REVISION,
            "--session-id",
            _SESSION_ID,
            "-w",
            workspace_id,
        ],
    )

    assert all(result.exit_code == 0 for result in (opened, read, updated, stopped))
    assert json.loads(stopped.output)["diagram"]["mode"] == "read_only"
    assert calls[0][1][1].path == "design.drawio"
    assert calls[1] == ("read", workspace_id)
    assert calls[2][1][1].expected_revision == _REVISION
    assert calls[2][1][1].xml == _XML
    assert calls[3][1][1].session_id == _SESSION_ID


def test_diagram_input_reader_bounds_bytes_and_rejects_non_utf8(tmp_path: Path) -> None:
    oversized = tmp_path / "too-large.drawio"
    oversized.write_bytes(b"x" * (_MAX_XML_BYTES + 1))
    with pytest.raises(GroveError, match="exceeds"):
        _validate_input(oversized)

    non_utf8 = tmp_path / "not-utf8.drawio"
    non_utf8.write_bytes(b"\xff")
    with pytest.raises(GroveError, match="not UTF-8"):
        _validate_input(non_utf8)
