"""A remote worker's transcript chunks land under its workspace's own config dir,
only for its own session, and only at the host's current size (#1099).

The route is the one road a remote session's CONVERSATION has onto this host,
so the fence is the same one ``/spool`` carries (bearer, generation, own
session), and the offset rule is what makes a reconnect replay harmless. The
registration half is pinned too: a worker that declares it mirrors gets a
``TranscriptContext`` recorded on its REMOTE record, which is what turns every
host reader onto the mirrored copy.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from grove.core import paths
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.native_owners import OwnerIdentity
from grove.core.runtimes import RemoteHandle
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import Runtime, TranscriptContext
from grove.daemon import build_app

from .test_mailboxes import _WORKSPACE, _config, _state, _write_project_config

_GENERATION = "b" * 32
_SESSION = "11111111-2222-3333-4444-555555555555"
_RELATIVE = "projects/-sandbox-work/11111111-2222-3333-4444-555555555555.jsonl"


def _app(tmp_state_dir: Path, *, remote: bool = True) -> tuple[FastAPI, JsonWorkspaceStore]:
    repo = tmp_state_dir / "repo"
    _write_project_config(repo)
    store = JsonWorkspaceStore()
    state = _state(_WORKSPACE, repo)
    if remote:
        state = replace(
            state, runtime=Runtime.REMOTE, remote=RemoteHandle(runtime="sandbox", ref="sbx-1")
        )
    store.save(state)
    return build_app(cfg=_config(auth=False), store=store), store


def _register(app: FastAPI) -> None:
    app.state.native_owners.register(
        OwnerIdentity(address=MailboxAddress(workspace_id=_WORKSPACE), generation=_GENERATION),
        _SESSION,
    )


def _register_over_the_route(app: FastAPI, **declared: str) -> int:
    """Register through the real ``/mailboxes/connection`` endpoint.

    Invoked directly rather than streamed through ``TestClient``: the stream
    never ends on its own, and the sync client deadlocks on it (the daemon
    guide's rule for every infinite stream here). The registration side effects
    happen before the response is returned, which is all these tests read.
    """
    route = next(
        route
        for route in app.state.mailbox_router.router().routes
        if getattr(route, "path", "") == "/mailboxes/connection"
    )

    async def register() -> int:
        response = await asyncio.wait_for(
            route.endpoint(
                workspace_id=_WORKSPACE,
                provider_session_id=_SESSION,
                input_capacity=None,
                pending_input_ids=None,
                live=False,
                reads=False,
                transcripts=declared.get("transcripts") == "1",
                cwd=declared.get("cwd"),
                _=None,
            ),
            timeout=1,
        )
        return int(response.status_code)

    return asyncio.run(register())


def _chunk(
    client: TestClient, data: bytes, offset: int, *, session: str = _SESSION, path: str = _RELATIVE
) -> tuple[int, dict[str, object]]:
    response = client.post(
        "/mailboxes/transcript",
        params={"workspace_id": _WORKSPACE, "generation": _GENERATION},
        json={
            "session_id": session,
            "relative_path": path,
            "offset": offset,
            "data": base64.b64encode(data).decode(),
        },
    )
    return response.status_code, response.json()


def test_chunks_land_in_the_workspace_config_dir_and_the_size_is_answered(
    tmp_state_dir: Path,
) -> None:
    app, _ = _app(tmp_state_dir)
    with TestClient(app) as client:
        _register(app)
        first = _chunk(client, b'{"type":"user"}\n', 0)
        second = _chunk(client, b'{"type":"assistant"}\n', 16)
    assert (first, second) == ((200, {"size": 16}), (200, {"size": 37}))
    copy = paths.agent_workspace_config_dir(_WORKSPACE) / _RELATIVE
    assert copy.read_bytes() == b'{"type":"user"}\n{"type":"assistant"}\n'


def test_a_chunk_at_the_wrong_offset_is_409_carrying_the_real_size(tmp_state_dir: Path) -> None:
    """A reconnect replay of chunk one must not double it: delete the offset
    check and the file below holds the line twice."""
    app, _ = _app(tmp_state_dir)
    with TestClient(app) as client:
        _register(app)
        _chunk(client, b"abc\n", 0)
        status, body = _chunk(client, b"abc\n", 0)
    assert status == 409
    assert body["detail"]["size"] == 4
    copy = paths.agent_workspace_config_dir(_WORKSPACE) / _RELATIVE
    assert copy.read_bytes() == b"abc\n"


def test_a_chunk_naming_another_session_or_escaping_the_root_writes_nothing(
    tmp_state_dir: Path,
) -> None:
    app, _ = _app(tmp_state_dir)
    stranger = "99999999-8888-7777-6666-555555555555"
    with TestClient(app) as client:
        _register(app)
        foreign, _ = _chunk(client, b"x", 0, session=stranger)
        escape, _ = _chunk(client, b"x", 0, path="../../outside.jsonl")
        absolute, _ = _chunk(client, b"x", 0, path="/tmp/outside.jsonl")
    assert (foreign, escape, absolute) == (403, 422, 422)
    root = paths.agent_workspace_config_dir(_WORKSPACE)
    assert not root.exists() or not any(root.rglob("*"))


def test_an_unregistered_generation_is_refused(tmp_state_dir: Path) -> None:
    app, _ = _app(tmp_state_dir)
    with TestClient(app) as client:
        status, _ = _chunk(client, b"x", 0)
    assert status in {404, 409}


def test_a_mirroring_registration_records_the_transcript_context(tmp_state_dir: Path) -> None:
    """From this record every host reader resolves the mirror: the config dir
    is the workspace's own, the cwd is the WORKLOAD's, not any host path."""
    app, store = _app(tmp_state_dir)
    with TestClient(app):
        assert _register_over_the_route(app, transcripts="1", cwd="/sandbox/work") == 200
    context = store.get(_WORKSPACE).transcript_context
    assert context is not None
    assert context.agent_cwd == "/sandbox/work"
    assert Path(context.config_dir) == paths.agent_workspace_config_dir(_WORKSPACE)


def test_a_registration_without_the_declaration_records_nothing(tmp_state_dir: Path) -> None:
    """An older worker keeps exactly today's behaviour."""
    app, store = _app(tmp_state_dir)
    with TestClient(app):
        assert _register_over_the_route(app) == 200
    assert store.get(_WORKSPACE).transcript_context is None


def test_a_host_workspace_keeps_its_launch_backend_context(tmp_state_dir: Path) -> None:
    """The record is only moved for a REMOTE runtime: a host launch's context
    was decided by its backend, and a worker declaring otherwise is ignored."""
    app, store = _app(tmp_state_dir, remote=False)
    with TestClient(app):
        assert _register_over_the_route(app, transcripts="1", cwd="/sandbox/work") == 200
    assert store.get(_WORKSPACE).transcript_context is None


def _pane(client: TestClient, first: int, lines: list[str], *, reset: bool = False) -> int:
    return client.post(
        "/mailboxes/pane",
        params={"workspace_id": _WORKSPACE, "generation": _GENERATION},
        json={"first": first, "lines": lines, "reset": reset},
    ).status_code


def test_pane_lines_reach_a_watching_replica_and_a_gap_is_409(tmp_state_dir: Path) -> None:
    """#1101: the replica exists only while someone watches, and a batch that
    does not continue it is refused rather than leaving a hole."""
    app, _ = _app(tmp_state_dir)
    with TestClient(app) as client:
        hub = app.state.mailbox_router._live
        _register(app)
        assert _pane(client, 0, ["dropped"], reset=True) == 200
        assert hub.pane(_WORKSPACE) == (None, None)
        wake = hub.join_pane(_WORKSPACE)
        assert _pane(client, 0, ["a", "b"], reset=True) == 200
        assert _pane(client, 2, ["c"]) == 200
        assert _pane(client, 9, ["x"]) == 409
        assert hub.pane(_WORKSPACE)[0] == "a\nb\nc"
        hub.leave_pane(_WORKSPACE, wake)
        assert hub.pane(_WORKSPACE) == (None, None)


def test_a_fresh_workload_reads_back_its_own_mirrored_session_only(tmp_state_dir: Path) -> None:
    """#1102: the seed routes serve the record's own session and nothing else."""
    app, store = _app(tmp_state_dir)
    store.save(
        replace(
            store.get(_WORKSPACE),
            agent_session_id=_SESSION,
            transcript_context=None,
        )
    )
    with TestClient(app) as client:
        _register(app)
        _chunk(client, b'{"type":"user"}\n', 0)
        store.save(
            replace(
                store.get(_WORKSPACE),
                transcript_context=TranscriptContext(
                    config_dir=str(paths.agent_workspace_config_dir(_WORKSPACE)),
                    agent_cwd="/sandbox/work",
                ),
            )
        )
        params = {"workspace_id": _WORKSPACE, "session_id": _SESSION}
        listed = client.get("/mailboxes/transcript/files", params=params).json()
        read = client.get(
            "/mailboxes/transcript", params={**params, "relative_path": _RELATIVE}
        ).json()
        stranger = client.get(
            "/mailboxes/transcript/files",
            params={**params, "session_id": "99999999-8888-7777-6666-555555555555"},
        ).json()
        unlisted = client.get(
            "/mailboxes/transcript", params={**params, "relative_path": "projects/x.jsonl"}
        )
    assert listed == [{"relative_path": _RELATIVE, "size": 16}]
    assert base64.b64decode(read["data"]) == b'{"type":"user"}\n'
    assert stranger == []
    assert unlisted.status_code == 404
