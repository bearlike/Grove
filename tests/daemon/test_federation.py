"""Two daemons in one process: join, register, and a verb forwarded to its owner.

The node's workspace route is a stub behind its own session gate, because the
contract under test is the federation hop, not any one verb. Real routes reach
the forward exactly the same way, since the proxy rewrites before routing.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from grove.core.auth import SessionStore
from grove.core.config import FederationConfig
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.federation import Hub, NodeBearer, NodeCatalogFile, NodeKey, PeerLink
from grove.daemon.auth import make_require_session
from grove.daemon.federation import Federation

_WS = "a" * 32
_STRANGER = "b" * 32
_CREATED = "c" * 32
# A real name: `ping`'s first hop resolves it. The ASGI transports ignore hosts.
_HUB_URL = "http://localhost"
_PROJECT = ProjectIdentity(remote="git.example.com/org/repo", default_branch="main")


def _resolve_create(request: CreateWorkspaceRequest) -> tuple[ProjectIdentity, dict[str, str]]:
    """The hub's resolver without a registry: the request's project and selector."""
    return request.project or _PROJECT, dict(request.selector or {})


def _cfg(role: str) -> FederationConfig:
    node = {"role": role}
    if role == "node":
        node |= {"hub_url": _HUB_URL, "advertise_url": "http://node"}
    return FederationConfig.model_validate({"enabled": True, "node": node})


def _app(federation: Federation, sessions: SessionStore, *, routes: bool) -> FastAPI:
    app = FastAPI()
    require_session = make_require_session(auth_store=sessions, enabled=True)
    if routes:

        @app.get("/workspaces/{ws_id}/peek", dependencies=[Depends(require_session)])
        async def peek(ws_id: str, request: Request) -> dict[str, str]:
            return {"answered_by": "node", "ws": ws_id, "q": request.url.query}

        @app.post("/workspaces", dependencies=[Depends(require_session)])
        async def create(request: Request) -> dict[str, object]:
            return {"id": _CREATED, "received": await request.json()}

    Federation.mount(
        federation,
        app,
        session_store=sessions,
        authenticate_caller=require_session,
        resolve_create=_resolve_create,
    )
    return app


@pytest.fixture
def pair(tmp_path: Path):
    hub_sessions = SessionStore(path=tmp_path / "hub-auth.json")
    node_sessions = SessionStore(path=tmp_path / "node-auth.json")
    # The hub's liveness clock; a test moves it to put a node out of sight.
    clock = [time.time()]
    hub = Hub(
        NodeCatalogFile(tmp_path / "nodes.json"),
        unreachable_after_seconds=60,
        max_clock_skew_seconds=60,
        join_token_ttl=timedelta(minutes=5),
        clock=lambda: clock[0],
    )
    node_key = NodeKey(tmp_path / "node-key")
    node_fed = Federation(
        _cfg("node"), hub=None, link=None, node_key=node_key, auth_dep=lambda: None
    )
    node_app = _app(node_fed, node_sessions, routes=True)
    hub_fed = Federation(
        _cfg("hub"),
        hub=hub,
        link=None,
        node_key=None,
        auth_dep=make_require_session(auth_store=hub_sessions, enabled=True),
        transport=httpx.ASGITransport(app=node_app),
    )
    hub_app = _app(hub_fed, hub_sessions, routes=False)
    return hub, hub_app, hub_sessions, node_key, node_sessions, clock


def _link(hub_app: FastAPI, key: NodeKey, token: str | None, workspaces: list[str]) -> PeerLink:
    return PeerLink(
        hub_url=_HUB_URL,
        name="node-a",
        advertise_url="http://node",
        key=key,
        join_token=lambda: token,
        workspaces=lambda: workspaces,
        projects=lambda: [_PROJECT],
        labels={"residency": "eu"},
        capacity=None,
        runtimes=("sbx",),
        heartbeat_seconds=60,
        reconnect_ceiling_seconds=1,
        timeout_seconds=5,
        transport=httpx.ASGITransport(app=hub_app),
    )


async def _one_beat(link: PeerLink) -> None:
    task = asyncio.create_task(link.run())
    for _ in range(200):
        if link.status.state in ("connected", "refused"):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_join_register_and_forward_a_verb_to_its_owner(pair) -> None:
    hub, hub_app, hub_sessions, node_key, _, _clock = pair
    link = _link(hub_app, node_key, hub.mint_join_token(), [_WS])
    await _one_beat(link)
    assert link.status.state == "connected"
    assert node_key.load() is not None

    caller = hub_sessions.mint_local("test-human")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hub_app), base_url="http://hub"
    ) as client:
        listed = (await client.get("/nodes", headers={"Authorization": f"Bearer {caller}"})).json()
        assert [(n["name"], n["state"], n["workspaces"]) for n in listed] == [
            ("node-a", "reachable", 1)
        ]
        forwarded = await client.get(
            f"/workspaces/{_WS}/peek?x=1", headers={"Authorization": f"Bearer {caller}"}
        )
        assert forwarded.status_code == 200
        assert forwarded.json() == {"answered_by": "node", "ws": _WS, "q": "x=1"}
        # The hub's own gate runs before anything is sent on.
        anonymous = await client.get(f"/workspaces/{_WS}/peek")
        assert anonymous.status_code == 401
        # A workspace no node owns is the hub's own business (here: no route).
        assert (
            await client.get(
                f"/workspaces/{_STRANGER}/peek", headers={"Authorization": f"Bearer {caller}"}
            )
        ).status_code == 404


async def test_a_node_without_a_key_cannot_register_and_a_hub_without_it_refuses(
    pair,
) -> None:
    hub, hub_app, _, node_key, _, _clock = pair
    keyless = _link(hub_app, node_key, None, [_WS])
    await _one_beat(keyless)
    assert keyless.status.state == "joining"
    assert hub.nodes() == []

    link = _link(hub_app, node_key, hub.mint_join_token(), [_WS])
    await _one_beat(link)
    node_id = (node_key.load() or ("", ""))[0]
    assert hub.forget(node_id)
    refused = _link(hub_app, node_key, None, [_WS])
    await _one_beat(refused)
    # A key refusal ENDS the link; a transport failure never does.
    assert refused.status.state == "refused"
    assert "hub:" in (refused.status.last_error or "")


async def test_a_forward_without_the_hubs_signature_is_refused_by_the_node(pair) -> None:
    hub, hub_app, _, node_key, _, _clock = pair
    await _one_beat(_link(hub_app, node_key, hub.mint_join_token(), [_WS]))
    assert hub.owner(_WS) is not None
    # Straight at the node, bypassing the hub, with no hub-signed bearer.
    federation_node_app = _app(
        Federation(_cfg("node"), hub=None, link=None, node_key=node_key, auth_dep=lambda: None),
        SessionStore(path=node_key.path.parent / "x-auth.json"),
        routes=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=federation_node_app), base_url="http://node"
    ) as client:
        response = await client.get(f"/federation/forward/workspaces/{_WS}/peek")
    assert response.status_code == 401
    assert response.json()["detail"]["message"].startswith("node:")


async def test_a_known_node_id_with_the_wrong_key_cannot_register(pair) -> None:
    """Knowing a node's id is not holding its key: the signature is what admits."""
    hub, hub_app, _, node_key, _, _clock = pair
    await _one_beat(_link(hub_app, node_key, hub.mint_join_token(), [_WS]))
    node_id = (node_key.load() or ("", ""))[0]
    forged = NodeBearer.sign(node_id, "not-the-key", audience="hub")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hub_app), base_url="http://hub"
    ) as client:
        response = await client.post(
            "/federation/register",
            json={"name": "evil", "advertise_url": "http://evil", "workspaces": [_STRANGER]},
            headers={"Authorization": f"Bearer {forged}"},
        )
    assert response.status_code == 401
    assert response.json()["detail"]["message"].startswith("hub:")
    assert hub.owner(_STRANGER) is None


def _node_app_with_link(link: PeerLink, key: NodeKey, sessions: SessionStore) -> FastAPI:
    require_session = make_require_session(auth_store=sessions, enabled=True)
    federation = Federation(
        _cfg("node"), hub=None, link=link, node_key=key, auth_dep=require_session
    )
    return _app(federation, sessions, routes=False)


async def test_node_self_service_join_status_and_hop_by_hop_ping(pair) -> None:
    """The routes `grove node join|status|ping` are shells over."""
    hub, hub_app, _, node_key, node_sessions, _clock = pair
    link = _link(hub_app, node_key, None, [_WS])
    node_app = _node_app_with_link(link, node_key, node_sessions)
    caller = {"Authorization": f"Bearer {node_sessions.mint_local('test-human')}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=node_app), base_url="http://node"
    ) as client:
        before = (await client.post("/nodes/self/ping", headers=caller)).json()
        assert [(h["hop"], h["ok"]) for h in before["hops"]] == [
            ("resolve", True),
            ("connect", True),
            ("auth", False),
        ]
        assert before["hops"][-1]["detail"].startswith("node: no key")

        joined = await client.post(
            "/nodes/self/join", json={"token": hub.mint_join_token()}, headers=caller
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["node_id"] == (node_key.load() or ("",))[0]

        after = (await client.post("/nodes/self/ping", headers=caller)).json()
        assert all(h["ok"] for h in after["hops"]), after
        hub.forget(joined.json()["node_id"])
        refused = (await client.post("/nodes/self/ping", headers=caller)).json()
        assert (refused["hops"][-1]["hop"], refused["hops"][-1]["ok"]) == ("auth", False)
        assert refused["hops"][-1]["detail"].startswith("hub:")

        status = (await client.get("/nodes/self", headers=caller)).json()
        assert status["role"] == "node" and status["hub_url"] == _HUB_URL


def _routes_for_mail_and_digest(app: FastAPI, sessions: SessionStore, who: str) -> None:
    """Stand-ins for the real mailbox/watch/digest routes, behind the same session gate."""
    require_session = make_require_session(auth_store=sessions, enabled=True)
    gate = [Depends(require_session)]

    @app.get("/mailboxes/contacts", dependencies=gate)
    async def contacts() -> dict[str, object]:
        limit = 1000 if who == "hub" else 500
        return {"protocol_version": 2, "body_limit_bytes": limit, "contacts": [{"who": who}]}

    @app.post("/mailboxes/messages", dependencies=gate)
    async def send(request: Request) -> dict[str, object]:
        return {"delivered_by": who, "body": await request.json()}

    @app.post("/watches", dependencies=gate)
    async def watch(request: Request) -> dict[str, object]:
        return {"registered_by": who}

    @app.post("/workspaces", dependencies=gate)
    async def create() -> dict[str, object]:
        return {"created_by": who}

    @app.get("/fleet/digest", dependencies=gate)
    async def digest() -> dict[str, object]:
        return {
            "observed_at": "2026-10-21T00:00:00Z",
            "totals": {"workspaces": 1, "by_status": {"active": 1}},
            "rows": [{"who": who}],
            "removed": [],
            "degraded_repos": [],
            "cursor": f"{who}-cursor",
            "next_page": f"{who}-page",
            "resync_required": False,
        }


@pytest.fixture
def wired(tmp_path: Path):
    """A hub and one joined node, both serving the mail/watch/digest stand-ins."""
    hub_sessions = SessionStore(path=tmp_path / "hub-auth.json")
    node_sessions = SessionStore(path=tmp_path / "node-auth.json")
    hub = Hub(
        NodeCatalogFile(tmp_path / "nodes.json"),
        unreachable_after_seconds=60,
        max_clock_skew_seconds=60,
        join_token_ttl=timedelta(minutes=5),
    )
    node_key = NodeKey(tmp_path / "node-key")
    node_app = FastAPI()
    _routes_for_mail_and_digest(node_app, node_sessions, "node")
    hub_app = FastAPI()
    _routes_for_mail_and_digest(hub_app, hub_sessions, "hub")
    hub_gate = make_require_session(auth_store=hub_sessions, enabled=True)
    Federation.mount(
        Federation(
            _cfg("hub"),
            hub=hub,
            link=None,
            node_key=None,
            auth_dep=hub_gate,
            transport=httpx.ASGITransport(app=node_app),
        ),
        hub_app,
        session_store=hub_sessions,
        authenticate_caller=hub_gate,
        resolve_create=_resolve_create,
    )
    link = _link(hub_app, node_key, hub.mint_join_token(), [_WS])
    node_gate = make_require_session(auth_store=node_sessions, enabled=True)
    Federation.mount(
        Federation(_cfg("node"), hub=None, link=link, node_key=node_key, auth_dep=node_gate),
        node_app,
        session_store=node_sessions,
        authenticate_caller=node_gate,
        resolve_create=_resolve_create,
    )
    caller = {"Authorization": f"Bearer {hub_sessions.mint_local('test-human')}"}
    node_caller = {"Authorization": f"Bearer {node_sessions.mint_local('test-human')}"}
    return hub_app, node_app, link, caller, node_caller


async def test_mail_and_watches_reach_the_owner_and_reads_are_a_union(wired) -> None:
    hub_app, _, link, caller, _ = wired
    await _one_beat(link)
    to = {"recipient": {"workspace_id": _WS, "agent": ""}}
    stranger = {"recipient": {"workspace_id": _STRANGER, "agent": ""}}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hub_app), base_url="http://hub"
    ) as client:
        sent = (await client.post("/mailboxes/messages", json=to, headers=caller)).json()
        assert sent == {"delivered_by": "node", "body": to}
        local = (await client.post("/mailboxes/messages", json=stranger, headers=caller)).json()
        assert local == {"delivered_by": "hub", "body": stranger}
        watched = (await client.post("/watches", json=to, headers=caller)).json()
        assert watched == {"registered_by": "node"}

        contacts = (await client.get("/mailboxes/contacts", headers=caller)).json()
        assert contacts["contacts"] == [{"who": "hub"}, {"who": "node"}]
        assert contacts["body_limit_bytes"] == 500

        digest = (await client.get("/fleet/digest", headers=caller)).json()
        assert digest["rows"] == [{"who": "hub"}, {"who": "node"}]
        assert digest["totals"]["workspaces"] == 2
        assert digest["totals"]["by_status"] == {"active": 2}
        # A cursor names ONE daemon's projection, so a union answers whole and says so.
        assert digest["next_page"] is None and digest["resync_required"] is False
        resumed = (await client.get("/fleet/digest?since=x", headers=caller)).json()
        assert resumed["resync_required"] is True

        anonymous = await client.get("/mailboxes/contacts")
        assert anonymous.status_code == 401


async def test_a_draining_node_refuses_new_workspaces_only(wired) -> None:
    _, node_app, link, _, node_caller = wired
    await _one_beat(link)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=node_app), base_url="http://node"
    ) as client:
        drained = await client.post("/nodes/self/drain", json={}, headers=node_caller)
        assert drained.json()["draining"] is True
        refused = await client.post("/workspaces", headers=node_caller)
        assert refused.status_code == 409
        assert refused.json()["detail"]["error"] == "node_draining"
        # Everything else still works: running workspaces finish.
        assert (
            await client.post(
                "/mailboxes/messages",
                json={"recipient": {"workspace_id": _WS}},
                headers=node_caller,
            )
        ).status_code == 200
        await client.post("/nodes/self/drain", json={"draining": False}, headers=node_caller)
        assert (await client.post("/workspaces", headers=node_caller)).status_code == 200


async def test_a_create_at_the_hub_is_placed_on_a_node_by_project_and_selector(
    pair,
) -> None:
    hub, hub_app, hub_sessions, node_key, _, _clock = pair
    await _one_beat(_link(hub_app, node_key, hub.mint_join_token(), [_WS]))
    caller = {"Authorization": f"Bearer {hub_sessions.mint_local('test-human')}"}
    create = {"agent_name": "claude", "title": "placed", "repo_root": "/hub/only/path"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hub_app), base_url="http://hub"
    ) as client:
        placed = await client.post(
            "/workspaces", json={**create, "selector": {"residency": "eu"}}, headers=caller
        )
        assert placed.status_code == 200, placed.text
        received = placed.json()["received"]
        # The node is told the project by name; the hub's path means nothing there.
        assert "repo_root" not in received
        assert received["project"] == _PROJECT.model_dump()
        # The hub knows the new workspace's owner before the next heartbeat.
        assert hub.owner(_CREATED) is not None

        refused = await client.post(
            "/workspaces", json={**create, "selector": {"residency": "us"}}, headers=caller
        )
        assert refused.status_code == 409
        assert refused.json()["detail"]["error"] == "placement_refused"
        assert "labels" in refused.json()["detail"]["message"]
        assert (await client.post("/workspaces", json=create)).status_code == 401

        remote = {**create, "runtime": "remote", "node": "node-a"}
        onto = await client.post(
            "/workspaces", json={**remote, "remote_runtime": "sbx"}, headers=caller
        )
        assert onto.status_code == 200, onto.text
        assert onto.json()["received"]["remote_runtime"] == "sbx"
        lacking = await client.post(
            "/workspaces", json={**remote, "remote_runtime": "gcp"}, headers=caller
        )
        assert lacking.status_code == 409
        assert "configures runtime gcp" in lacking.json()["detail"]["message"]


async def test_a_verb_on_an_unreachable_owners_workspace_is_a_typed_503(
    pair,
) -> None:
    hub, hub_app, hub_sessions, node_key, _, clock = pair
    await _one_beat(_link(hub_app, node_key, hub.mint_join_token(), [_WS]))
    clock[0] += 61  # silent past `unreachable_after_seconds`: out of sight, not gone
    caller = {"Authorization": f"Bearer {hub_sessions.mint_local('test-human')}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=hub_app), base_url="http://hub"
    ) as client:
        unreachable = await client.get(f"/workspaces/{_WS}/peek", headers=caller)
        assert unreachable.status_code == 503
        assert unreachable.json()["detail"]["error"] == "owner_unreachable"
        # Who owns a workspace is not an anonymous caller's to learn.
        assert (await client.get(f"/workspaces/{_WS}/peek")).status_code == 401
