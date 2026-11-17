"""A remote worker's spool drops reach the host sidecar, and only for its own session (#1030).

The route is the one road a remote session's agent state has onto this host, so
its fence is the property worth pinning: the bearer and generation like
``/pulse``, and a drop that names any session but the owner's own is refused.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core import paths
from grove.core.agents.hook import ClaudeHook
from grove.core.agents.model import AgentActivityState
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.native_owners import OwnerIdentity
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app

from .test_mailboxes import _WORKSPACE, _config, _state, _write_project_config

_GENERATION = "b" * 32
_SESSION = "11111111-2222-3333-4444-555555555555"


def _app(tmp_state_dir: Path) -> FastAPI:
    repo = tmp_state_dir / "repo"
    _write_project_config(repo)
    store = JsonWorkspaceStore()
    store.save(_state(_WORKSPACE, repo))
    return build_app(cfg=_config(auth=False), store=store)


def _register(app: FastAPI) -> None:
    app.state.native_owners.register(
        OwnerIdentity(address=MailboxAddress(workspace_id=_WORKSPACE), generation=_GENERATION),
        _SESSION,
    )


def _post(client: TestClient, body: dict[str, object], suffix: str = ".turn.json") -> int:
    response = client.post(
        "/mailboxes/spool",
        params={"workspace_id": _WORKSPACE, "generation": _GENERATION, "suffix": suffix},
        json=body,
    )
    return response.status_code


def test_a_turn_end_from_the_owner_lands_in_its_sidecar(tmp_state_dir: Path) -> None:
    app = _app(tmp_state_dir)
    with TestClient(app) as client:
        _register(app)
        status = _post(client, {"hook_event_name": "Stop", "session_id": _SESSION})
    assert status == 200
    record = ClaudeHook.read(_SESSION, sidecar_dir=paths.agent_sidecar_dir())
    assert record is not None
    assert record.state is AgentActivityState.WAITING


def test_a_drop_naming_another_session_is_refused_and_writes_nothing(
    tmp_state_dir: Path,
) -> None:
    """The owner is registered and its generation is right, so ONLY the session
    check can refuse this drop: delete it and the stranger's sidecar is written."""
    app = _app(tmp_state_dir)
    stranger = "99999999-8888-7777-6666-555555555555"
    with TestClient(app) as client:
        _register(app)
        status = _post(client, {"hook_event_name": "Stop", "session_id": stranger})
    assert status == 403
    assert ClaudeHook.read(stranger, sidecar_dir=paths.agent_sidecar_dir()) is None


def test_an_unregistered_owner_and_an_unknown_arm_are_refused(tmp_state_dir: Path) -> None:
    app = _app(tmp_state_dir)
    with TestClient(app) as client:
        assert _post(client, {"hook_event_name": "Stop", "session_id": _SESSION}) == 404
        _register(app)
        assert _post(client, {"session_id": _SESSION}, suffix=".statusline.json") == 422
    assert ClaudeHook.read(_SESSION, sidecar_dir=paths.agent_sidecar_dir()) is None
