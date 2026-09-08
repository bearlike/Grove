"""Diagram HTTP-client request shaping.

The document protocol is a typed REST pass-through. A workspace id selects its
manager; callers may add a repo selector only when they already hold one.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from grove.client import BackendConfig, GroveClient
from grove.core.contracts.diagrams import (
    DiagramOpenRequest,
    DiagramPreviewUploadRequest,
    DiagramStopRequest,
    DiagramUpdateRequest,
)

_SESSION_ID = "a" * 32
_REVISION = "b" * 64
_XML = '<mxfile host="app.diagrams.net"><diagram id="page">x</diagram></mxfile>'
_DOCUMENT = {
    "diagram": {"path": "design.drawio", "session_id": _SESSION_ID, "mode": "active"},
    "revision": _REVISION,
    "xml": _XML,
}
_PREVIEW = {
    "session_id": _SESSION_ID,
    "revision": _REVISION,
    "page_index": 0,
    "attachment": {"id": "a" * 12, "name": "diagram-preview.png", "path": "/preview.png"},
    "mime_type": "image/png",
    "content_base64": "iVBORw0KGgo=",
}


def _client(handler: httpx.MockTransport) -> GroveClient:
    client = GroveClient(BackendConfig(label="diagram"))
    client._http = httpx.AsyncClient(base_url="http://daemon.test", transport=handler)
    return client


async def test_diagram_methods_use_frozen_routes_without_requiring_a_repo_selector() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_DOCUMENT)

    client = _client(httpx.MockTransport(handler))
    try:
        opened = await client.open_diagram("ws-1", DiagramOpenRequest(path="design.drawio"))
        read = await client.read_diagram("ws-1")
        updated = await client.update_diagram(
            "ws-1",
            DiagramUpdateRequest(session_id=_SESSION_ID, expected_revision=_REVISION, xml=_XML),
        )
        stopped = await client.stop_diagram(
            "ws-1", DiagramStopRequest(session_id=_SESSION_ID, expected_revision=_REVISION)
        )
    finally:
        await client.close()

    assert [doc.revision for doc in (opened, read, updated, stopped)] == [_REVISION] * 4
    assert [(r.method, r.url.path, r.url.params.get("repo")) for r in requests] == [
        ("POST", "/workspaces/ws-1/diagram", None),
        ("GET", "/workspaces/ws-1/diagram", None),
        ("PUT", "/workspaces/ws-1/diagram", None),
        ("POST", "/workspaces/ws-1/diagram/stop", None),
    ]
    assert json.loads(requests[0].content) == {"path": "design.drawio"}
    assert json.loads(requests[2].content) == {
        "session_id": _SESSION_ID,
        "expected_revision": _REVISION,
        "xml": _XML,
    }
    assert json.loads(requests[3].content) == {
        "session_id": _SESSION_ID,
        "expected_revision": _REVISION,
    }


async def test_preview_methods_use_the_revision_fenced_routes() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_PREVIEW)

    client = _client(httpx.MockTransport(handler))
    try:
        saved = await client.save_diagram_preview(
            "ws-1",
            DiagramPreviewUploadRequest(
                session_id=_SESSION_ID,
                expected_revision=_REVISION,
                content_base64="iVBORw0KGgo=",
            ),
        )
        read = await client.read_diagram_preview("ws-1")
    finally:
        await client.close()

    assert saved.attachment.name == read.attachment.name == "diagram-preview.png"
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/workspaces/ws-1/diagram/preview"),
        ("GET", "/workspaces/ws-1/diagram/preview"),
    ]
    assert json.loads(requests[0].content)["expected_revision"] == _REVISION


async def test_diagram_methods_forward_an_optional_repo_consistency_selector() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_DOCUMENT)

    client = _client(httpx.MockTransport(handler))
    try:
        await client.open_diagram(
            "ws-1", DiagramOpenRequest(path="design.drawio"), repo_root=Path("/repo")
        )
    finally:
        await client.close()

    assert requests[0].url.params["repo"] == "/repo"
