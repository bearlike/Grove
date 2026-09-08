"""GET /gallery and its per-item document + preview routes.

The gallery is a host-wide READ over ``.drawio`` files in known repos'
worktrees, attributed through the session catalog. These tests pin the census
(git-visible files AND the git-excluded ``.grove/attachments`` tree, nothing
outside a known repo), the attribution join (workspace by worktree, session by
cwd, liveness folded to one bit), the opaque-id contract (no path ever leaves
or enters), and the content-keyed preview cache.
"""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents.claude_code import _ClaudeHome
from grove.core.gallery import DiagramGallery
from grove.core.process import LiveRuntime
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus
from grove.daemon import build_app
from tests.daemon.conftest import daemon_test_config

SID = "66666666-6666-4666-8666-666666666666"
_XML = (
    '<mxfile><diagram id="p1" name="One"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram>'
    '<diagram id="p2" name="Two"><mxGraphModel><root>'
    '<mxCell id="0"/><mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>'
)
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=10)


def _write_transcript(claude_home: Path, sid: str, cwd: str, *, mtime: int) -> None:
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text(
        '{"type":"mode","mode":"normal"}\n'
        f'{{"type":"user","uuid":"h1","timestamp":"2026-06-09T08:00:00.000Z",'
        f'"isSidechain":false,"cwd":"{cwd}","gitBranch":"main",'
        '"message":{"role":"user","content":"hi"}}\n',
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


@pytest.fixture
def runtimes(monkeypatch: pytest.MonkeyPatch) -> list[LiveRuntime]:
    live: list[LiveRuntime] = []
    monkeypatch.setattr("grove.core.process.list_agent_runtimes", lambda **_: tuple(live))
    return live


@pytest.fixture(autouse=True)
def _offline_mewbo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("grove.core.agents.mewbo.MewboAdapter.list_sessions", lambda self, cwd: [])
    monkeypatch.setattr("grove.core.agents.mewbo.MewboAdapter.discover_all", lambda self: ())


@pytest.fixture
def repo(tmp_state_dir: Path) -> Path:
    root = tmp_state_dir / "repo-a"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "docs").mkdir()
    (root / "docs" / "tracked.drawio").write_text(_XML, encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "vendored.drawio").write_text(_XML, encoding="utf-8")
    (root / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    (root / "untracked.drawio").write_text(_XML, encoding="utf-8")
    excluded = root / ".grove" / "attachments" / "mock"
    excluded.mkdir(parents=True)
    (excluded / "board.drawio").write_text(_XML.replace("One", "Uno"), encoding="utf-8")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text(".grove/attachments/\n", encoding="utf-8")
    return root


@pytest.fixture
def client(
    repo: Path, claude_home: Path, runtimes: list[LiveRuntime], tmp_state_dir: Path
) -> Iterator[TestClient]:
    store = JsonWorkspaceStore()
    now = datetime.now(tz=UTC)
    store.save(
        WorkspaceState(
            id="a1",
            title="root work",
            repo_root=str(repo),
            branch="main",
            base_branch="main",
            worktree_path=str(repo),
            tmux_session="grove-a1",
            agent_name="claude",
            status=WorkspaceStatus.PAUSED,
            created_at=now,
            updated_at=now,
            agent_session_id=SID,
        )
    )
    _write_transcript(claude_home, SID, str(repo), mtime=5_000)
    # A diagram in a repo Grove knows nothing about must NOT appear.
    stranger = tmp_state_dir / "stranger"
    stranger.mkdir()
    (stranger / "secret.drawio").write_text(_XML, encoding="utf-8")
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as test_client:
        yield test_client


def test_gallery_lists_git_visible_and_attachment_diagrams_only(client: TestClient) -> None:
    resp = client.get("/gallery")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    rel = sorted(row["relative_path"] for row in rows)
    assert rel == [
        ".grove/attachments/mock/board.drawio",
        "docs/tracked.drawio",
        "untracked.drawio",
    ]
    # Ignored trees are not visited, strangers' repos are not scanned, and no
    # row carries a client-usable path in its id.
    assert all("secret" not in row["relative_path"] for row in rows)
    assert all(len(row["id"]) == 64 for row in rows)
    tracked = next(row for row in rows if row["relative_path"] == "docs/tracked.drawio")
    assert tracked["pages"] == 2
    assert tracked["name"] == "tracked.drawio"
    assert tracked["repo_name"] == "repo-a"
    assert tracked["preview_ready"] is False


def test_gallery_attributes_workspace_and_session_by_worktree(client: TestClient) -> None:
    row = client.get("/gallery").json()[0]
    assert row["workspace_id"] == "a1"
    assert row["workspace_title"] == "root work"
    assert row["workspace_branch"] == "main"
    # PAUSED reconciles to PAUSED — not live, so the client offers the session.
    assert row["workspace_live"] is False
    assert row["session_id"] == SID
    assert row["session_kind"] == "claude_code"
    assert row["session_live"] is False


def test_gallery_document_serves_bytes_by_opaque_id_and_404s_otherwise(
    client: TestClient,
) -> None:
    rows = client.get("/gallery").json()
    board = next(r for r in rows if r["name"] == "board.drawio")
    doc = client.get(f"/gallery/{board['id']}")
    assert doc.status_code == 200, doc.text
    assert doc.json()["xml"].count("<diagram") == 2
    assert "Uno" in doc.json()["xml"]
    assert doc.json()["digest"] == board["digest"]
    assert client.get("/gallery/not-an-id").status_code == 404
    assert client.get("/gallery/../../etc/passwd").status_code in (404, 422)


def test_gallery_preview_is_keyed_by_content_digest(client: TestClient) -> None:
    rows = client.get("/gallery").json()
    row = rows[0]
    assert client.get(f"/gallery/{row['id']}/preview").status_code == 404
    encoded = base64.b64encode(_PNG).decode("ascii")
    stale = client.post(
        f"/gallery/{row['id']}/preview", json={"digest": "0" * 64, "content_base64": encoded}
    )
    assert stale.status_code == 409
    saved = client.post(
        f"/gallery/{row['id']}/preview", json={"digest": row["digest"], "content_base64": encoded}
    )
    assert saved.status_code == 200, saved.text
    assert DiagramGallery.preview_path(row["digest"]).is_file()
    fetched = client.get(f"/gallery/{row['id']}/preview")
    assert fetched.status_code == 200
    assert base64.b64decode(fetched.json()["content_base64"]) == _PNG
    # The listing now says so, and a twin with the same bytes shares the render.
    listed = {r["relative_path"]: r for r in client.get("/gallery").json()}
    assert listed[row["relative_path"]]["preview_ready"] is True
    twins = [r for r in listed.values() if r["digest"] == row["digest"]]
    assert all(r["preview_ready"] for r in twins)
    # A non-PNG body is refused.
    bad = client.post(
        f"/gallery/{row['id']}/preview",
        json={"digest": row["digest"], "content_base64": base64.b64encode(b"nope").decode()},
    )
    # `WorkspaceStateError` → 409 is the daemon's standing mapping for a body
    # the engine refuses; the PNG signature check raises exactly that.
    assert bad.status_code == 409
    assert (
        base64.b64decode(client.get(f"/gallery/{row['id']}/preview").json()["content_base64"])
        == _PNG
    )
