"""Diagram MCP tools flatten the revision protocol without local persistence."""

from __future__ import annotations

import base64
import json
from typing import Any

from grove.core.contracts.diagrams import DiagramDocumentView, DiagramPreviewView
from grove.mcp.server import GroveMcpServer, McpServerConfig
from grove.mcp.tools import GroveTools
from tests.mcp.conftest import FakeGroveClient

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


class _DiagramClient(FakeGroveClient):
    async def open_diagram(self, workspace_id: str, request: Any) -> DiagramDocumentView:
        self.calls.append(("open_diagram", {"workspace_id": workspace_id, "request": request}))
        return _document()

    async def read_diagram(self, workspace_id: str) -> DiagramDocumentView:
        self.calls.append(("read_diagram", {"workspace_id": workspace_id}))
        return _document(mode="read_only")

    async def update_diagram(self, workspace_id: str, request: Any) -> DiagramDocumentView:
        self.calls.append(("update_diagram", {"workspace_id": workspace_id, "request": request}))
        return _document()

    async def stop_diagram(self, workspace_id: str, request: Any) -> DiagramDocumentView:
        self.calls.append(("stop_diagram", {"workspace_id": workspace_id, "request": request}))
        return _document(mode="read_only")


async def test_diagram_tools_need_only_workspace_identity_and_flatten_requests() -> None:
    client = _DiagramClient()
    tools = GroveTools(client)

    opened = await tools.open_diagram("ws-1", "design.drawio")
    read = await tools.read_diagram("ws-1")
    updated = await tools.update_diagram("ws-1", _SESSION_ID, _REVISION, _XML)
    stopped = await tools.stop_diagram("ws-1", _SESSION_ID, _REVISION)

    assert opened.diagram.mode == "active"
    assert read.diagram.mode == "read_only"
    assert updated.revision == _REVISION
    assert stopped.diagram.mode == "read_only"

    open_request = client.calls[0][1]["request"]
    update_request = client.calls[2][1]["request"]
    stop_request = client.calls[3][1]["request"]
    assert client.calls[0] == (
        "open_diagram",
        {"workspace_id": "ws-1", "request": open_request},
    )
    assert client.calls[1] == ("read_diagram", {"workspace_id": "ws-1"})
    assert client.calls[2] == (
        "update_diagram",
        {"workspace_id": "ws-1", "request": update_request},
    )
    assert client.calls[3] == (
        "stop_diagram",
        {"workspace_id": "ws-1", "request": stop_request},
    )
    assert open_request.path == "design.drawio"
    assert update_request.session_id == _SESSION_ID
    assert update_request.expected_revision == _REVISION
    assert update_request.xml == _XML
    assert stop_request.session_id == _SESSION_ID
    assert stop_request.expected_revision == _REVISION


async def test_preview_dispatch_returns_native_image_not_json_wrapper() -> None:
    png = b"\x89PNG\r\n\x1a\nsynthetic"

    class PreviewClient(FakeGroveClient):
        async def read_diagram_preview(self, workspace_id: str) -> DiagramPreviewView:
            return DiagramPreviewView(
                session_id=_SESSION_ID,
                revision=_REVISION,
                attachment={
                    "id": "a" * 12,
                    "name": "diagram-preview.png",
                    "path": "/tmp/synthetic.png",
                },
                content_base64=base64.b64encode(png).decode(),
            )

    server = GroveMcpServer(
        McpServerConfig(api_url="http://127.0.0.1:7421", api_token=None), client=PreviewClient()
    )
    result = await server._mcp.call_tool("grove_read_diagram_preview", {"workspace_id": "ws-1"})
    content = result[0] if isinstance(result, tuple) else result
    assert [block.type for block in content] == ["image", "text"]
    assert content[0].mimeType == "image/png"
    assert base64.b64decode(content[0].data) == png
    metadata = json.loads(content[1].text)
    assert metadata["revision"] == _REVISION
    assert metadata["page_index"] == 0
