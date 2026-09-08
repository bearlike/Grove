"""POST /workspaces/{id}/attachments, and the ids a message names.

The two-step shape is the contract: upload returns an id, and a message carries
ids rather than paths. A caller-supplied path would be the caller choosing which
file the agent is told to open, so the engine resolves an id against one
directory it owns and the route never accepts a path at all.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.attachments import AttachmentStore
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


@pytest.fixture
def daemon(tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    del tmp_repo, fake_tmux
    app = build_app(cfg=daemon_test_config(), store=JsonWorkspaceStore())
    with TestClient(app) as client:
        yield client


def _create(daemon: TestClient, tmp_repo: Path) -> str:
    return daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "attach test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()["id"]


def _upload(daemon: TestClient, ws_id: str, name: str, data: bytes) -> dict[str, str]:
    resp = daemon.post(
        f"/workspaces/{ws_id}/attachments",
        json={"name": name, "content_base64": base64.b64encode(data).decode("ascii")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_upload_returns_an_id_and_the_path_the_agent_will_read(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create(daemon, tmp_repo)

    body = _upload(daemon, ws_id, "diagram.png", b"\x89PNG\r\n\x1a\n")

    assert body["name"] == "diagram.png"
    assert AttachmentStore.RELDIR in body["path"]
    assert Path(body["path"]).read_bytes() == b"\x89PNG\r\n\x1a\n"


def test_binary_survives_the_json_round_trip(daemon: TestClient, tmp_repo: Path) -> None:
    """Base64-in-JSON exists because the browser's BFF proxy reads request
    bodies as TEXT, which would corrupt a multipart binary body. Every byte
    value has to come back exactly."""
    ws_id = _create(daemon, tmp_repo)
    payload = bytes(range(256))

    body = _upload(daemon, ws_id, "all-bytes.bin", payload)

    assert Path(body["path"]).read_bytes() == payload


def test_a_filename_that_is_a_path_is_sanitized_rather_than_refused(
    daemon: TestClient, tmp_repo: Path
) -> None:
    ws_id = _create(daemon, tmp_repo)

    body = _upload(daemon, ws_id, "../../../etc/passwd", b"nope")

    assert body["name"] == "passwd"
    assert AttachmentStore.RELDIR in body["path"]


def test_malformed_base64_is_422_and_names_itself(daemon: TestClient, tmp_repo: Path) -> None:
    ws_id = _create(daemon, tmp_repo)

    resp = daemon.post(
        f"/workspaces/{ws_id}/attachments",
        json={"name": "x.txt", "content_base64": "not base64 at all!!"},
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_attachment"


def test_uploading_to_an_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.post(
        "/workspaces/nope/attachments",
        json={"name": "x.txt", "content_base64": base64.b64encode(b"x").decode("ascii")},
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_a_message_naming_the_id_delivers_the_path(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    ws_id = _create(daemon, tmp_repo)
    uploaded = _upload(daemon, ws_id, "notes.md", b"read me")

    resp = daemon.post(
        f"/workspaces/{ws_id}/message",
        json={"text": "have a look", "attachments": [uploaded["id"]]},
    )

    assert resp.status_code == 204
    ((_target, text),) = fake_tmux.sent_texts
    assert text.startswith("have a look")
    assert '<grove-instruction kind="attachments">' in text
    assert uploaded["path"] in text


def test_a_message_with_no_attachments_key_still_works(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """The field defaults, so every client that predates it keeps working and
    its messages are byte-identical to what they were."""
    ws_id = _create(daemon, tmp_repo)

    resp = daemon.post(f"/workspaces/{ws_id}/message", json={"text": "plain"})

    assert resp.status_code == 204
    assert [text for _t, text in fake_tmux.sent_texts] == ["plain"]


def test_create_carries_attachments_so_the_landing_composer_needs_no_workspace_first(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux
) -> None:
    """The landing composer's whole path, through the real wire.

    It has no workspace id to upload against — the create IS the thing that
    makes one — so the files ride `POST /workspaces` and the engine stores them
    the moment the worktree exists. Asserted through the daemon rather than the
    manager because the point is that the field survives Pydantic's
    ``extra="forbid"`` envelope and arrives decoded.
    """
    resp = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "landing create",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
            "initial_prompt": "use the spec",
            "attachments": [
                {"name": "spec.md", "content_base64": base64.b64encode(b"# spec").decode("ascii")}
            ],
        },
    )

    assert resp.status_code == 200, resp.text
    state = resp.json()
    stored = list((Path(state["worktree_path"]) / AttachmentStore.RELDIR).rglob("spec.md"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == b"# spec"

    decoration = next(
        deco for session, deco in fake_tmux.launch_decorations if session == state["tmux_session"]
    )
    prompt = decoration[-1]
    assert "use the spec" in prompt
    assert '<grove-instruction kind="attachments">' in prompt
    assert str(stored[0]) in prompt


def test_a_create_naming_no_attachments_is_accepted_unchanged(
    daemon: TestClient, tmp_repo: Path
) -> None:
    """The field defaults, so every client that predates it still creates."""
    resp = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "no files",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    )

    assert resp.status_code == 200, resp.text
