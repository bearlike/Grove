"""Host-wide sidebar pins: ordering, idempotence, persistence, auth, concurrency."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.manager import WorkspaceManager
from grove.core.sidebar_pins import SidebarPinStore
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config


def _setup(tmp_repo: Path, tmp_path: Path, count: int = 3) -> tuple[Path, Path, list[str]]:
    state_path = tmp_path / "state.json"
    manager = WorkspaceManager(
        repo_root=tmp_repo, cfg=GroveConfig(), store=JsonWorkspaceStore(path=state_path)
    )
    ids = [
        manager.create(CreateWorkspaceRequest(agent_name="claude", title=f"pin {n}")).id
        for n in range(count)
    ]
    return state_path, tmp_path / "sidebar-pins.json", ids


def _client(state_path: Path, pins_path: Path, cfg: GroveConfig | None = None) -> TestClient:
    app = build_app(
        cfg=cfg or daemon_test_config(),
        store=JsonWorkspaceStore(path=state_path),
        sidebar_pin_store=SidebarPinStore(path=pins_path),
    )
    return TestClient(app)


def test_pins_are_newest_first_and_survive_restart(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state_path, pins_path, ids = _setup(tmp_repo, tmp_path)
    with _client(state_path, pins_path) as client:
        assert client.get("/sidebar/pins").json() == []
        for ws_id in ids:  # back-to-back: no clock tick may decide the order
            assert client.put(f"/sidebar/pins/{ws_id}").status_code == 200
        assert client.get("/sidebar/pins").json() == list(reversed(ids))
    # A new app over the same files is a daemon restart / a second browser.
    with _client(state_path, pins_path) as client:
        assert client.get("/sidebar/pins").json() == list(reversed(ids))


def test_repin_is_idempotent_and_keeps_position(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state_path, pins_path, ids = _setup(tmp_repo, tmp_path)
    with _client(state_path, pins_path) as client:
        client.put(f"/sidebar/pins/{ids[0]}")
        client.put(f"/sidebar/pins/{ids[1]}")
        client.put(f"/sidebar/pins/{ids[0]}")
        assert client.get("/sidebar/pins").json() == [ids[1], ids[0]]


def test_unpin_is_idempotent_and_repin_goes_to_top(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state_path, pins_path, ids = _setup(tmp_repo, tmp_path)
    with _client(state_path, pins_path) as client:
        client.put(f"/sidebar/pins/{ids[0]}")
        client.put(f"/sidebar/pins/{ids[1]}")
        assert client.delete(f"/sidebar/pins/{ids[0]}").status_code == 200
        assert client.delete(f"/sidebar/pins/{ids[0]}").status_code == 200
        assert client.delete(f"/sidebar/pins/{ids[2]}").status_code == 200  # never pinned
        assert client.get("/sidebar/pins").json() == [ids[1]]
        client.put(f"/sidebar/pins/{ids[0]}")
        assert client.get("/sidebar/pins").json() == [ids[0], ids[1]]


def test_pin_unknown_workspace_is_404_and_writes_nothing(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state_path, pins_path, _ = _setup(tmp_repo, tmp_path, count=1)
    with _client(state_path, pins_path) as client:
        response = client.put(f"/sidebar/pins/{'a' * 32}")
        assert response.status_code == 404
        assert response.json()["detail"]["error"] == "workspace_not_found"
        assert client.get("/sidebar/pins").json() == []
    assert not pins_path.exists()


def test_killed_workspace_pin_is_not_listed(
    tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux
) -> None:
    del fake_tmux
    state_path, pins_path, ids = _setup(tmp_repo, tmp_path, count=2)
    store = JsonWorkspaceStore(path=state_path)
    with _client(state_path, pins_path) as client:
        client.put(f"/sidebar/pins/{ids[0]}")
        client.put(f"/sidebar/pins/{ids[1]}")
        store.delete(ids[1])
        assert client.get("/sidebar/pins").json() == [ids[0]]


def test_concurrent_pins_lose_nothing(tmp_path: Path) -> None:
    store = SidebarPinStore(path=tmp_path / "pins.json")
    ids = [f"{n:032x}" for n in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(store.pin, ids))
    assert sorted(store.pinned()) == sorted(ids)


def test_routes_require_auth(tmp_repo: Path, tmp_path: Path, fake_tmux: FakeTmux) -> None:
    del fake_tmux
    state_path, pins_path, ids = _setup(tmp_repo, tmp_path, count=1)
    with _client(state_path, pins_path, cfg=GroveConfig()) as client:
        assert client.get("/sidebar/pins").status_code == 401
        assert client.put(f"/sidebar/pins/{ids[0]}").status_code == 401
        assert client.delete(f"/sidebar/pins/{ids[0]}").status_code == 401
