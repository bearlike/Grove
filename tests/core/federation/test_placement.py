"""Where a hub puts a new workspace: filter, refuse with a reason, least-loaded, no overcommit."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from grove.core.contracts.federation import RegisterRequest
from grove.core.contracts.project_identity import ProjectIdentity
from grove.core.federation import Hub, NodeCatalogFile
from grove.core.federation.hub import OwnerUnreachable, PlacementRefused

_REPO = ProjectIdentity(remote="git.example.com/org/repo", default_branch="main")


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def hub(tmp_path: Path) -> tuple[Hub, _Clock]:
    clock = _Clock()
    return (
        Hub(
            NodeCatalogFile(tmp_path / "nodes.json"),
            unreachable_after_seconds=60,
            max_clock_skew_seconds=60,
            join_token_ttl=timedelta(minutes=5),
            clock=clock,
        ),
        clock,
    )


def _node(hub: Hub, name: str, **register: object) -> str:
    record, _ = hub.join(hub.mint_join_token(), name=name, advertise_url=f"http://{name}")
    body = {"name": name, "advertise_url": f"http://{name}", "projects": [_REPO], **register}
    hub.register(record, RegisterRequest.model_validate(body))
    return record.node_id


def test_least_loaded_by_declared_capacity(hub: tuple[Hub, _Clock]) -> None:
    h, _ = hub
    big = _node(h, "big", capacity=10, workspaces=["a" * 32, "b" * 32])  # 20 % full
    _node(h, "small", capacity=2, workspaces=["c" * 32])  # 50 % full
    assert h.place(_REPO.remote, {}).node_id == big


@pytest.mark.parametrize(
    ("register", "selector", "reason"),
    [
        ({"draining": True}, {}, "draining"),
        ({"projects": []}, {}, "holds project"),
        ({"labels": {"residency": "us"}}, {"residency": "eu"}, "labels"),
        ({"capacity": 1, "workspaces": ["a" * 32]}, {}, "at capacity"),
    ],
)
def test_refuses_naming_the_filter_that_emptied_the_field(
    hub: tuple[Hub, _Clock], register: dict[str, object], selector: dict[str, str], reason: str
) -> None:
    h, _ = hub
    _node(h, "only", **register)
    with pytest.raises(PlacementRefused, match=reason):
        h.place(_REPO.remote, selector)


def test_an_unreachable_node_is_never_chosen(hub: tuple[Hub, _Clock]) -> None:
    h, clock = hub
    _node(h, "gone")
    clock.now += 61
    with pytest.raises(PlacementRefused, match="no reachable node"):
        h.place(_REPO.remote, {})


def test_a_reservation_survives_a_heartbeat_so_the_last_slot_is_given_once(
    hub: tuple[Hub, _Clock],
) -> None:
    h, _ = hub
    node_id = _node(h, "one", capacity=1)
    assert h.place(_REPO.remote, {}).node_id == node_id
    # The node beats again before the create confirms; its list is still empty.
    record = h.catalog.node(node_id)
    assert record is not None
    h.register(
        record,
        RegisterRequest(name="one", advertise_url="http://one", projects=[_REPO], capacity=1),
    )
    with pytest.raises(PlacementRefused, match="at capacity"):
        h.place(_REPO.remote, {})
    h.settle(node_id, None)  # the create failed: the slot comes back
    assert h.place(_REPO.remote, {}).node_id == node_id


def test_a_settled_create_is_owned_at_once_and_an_unreachable_owner_is_typed(
    hub: tuple[Hub, _Clock],
) -> None:
    h, clock = hub
    node_id = _node(h, "one")
    h.place(_REPO.remote, {})
    h.settle(node_id, "d" * 32)
    owner = h.owner("d" * 32)
    assert owner is not None and owner.node_id == node_id
    clock.now += 61
    with pytest.raises(OwnerUnreachable, match="on node one, last seen"):
        h.owner("d" * 32)
    assert h.owner("e" * 32) is None


def test_a_create_naming_a_runtime_lands_only_on_a_node_that_configures_it(
    hub: tuple[Hub, _Clock],
) -> None:
    h, _ = hub
    _node(h, "plain")
    sandbox = _node(h, "sandbox", runtimes=["sbx"], workspaces=["a" * 32])
    assert h.place(_REPO.remote, {}, runtime="sbx").node_id == sandbox
    with pytest.raises(PlacementRefused, match="no node configures runtime gcp"):
        h.place(_REPO.remote, {}, runtime="gcp")


def test_a_create_naming_a_node_lands_there_or_is_refused_by_name(
    hub: tuple[Hub, _Clock],
) -> None:
    h, _ = hub
    _node(h, "idle")
    busy = _node(h, "busy", workspaces=["a" * 32])
    assert h.place(_REPO.remote, {}, node="busy").node_id == busy
    with pytest.raises(PlacementRefused, match="no node is named gone"):
        h.place(_REPO.remote, {}, node="gone")
    with pytest.raises(PlacementRefused, match="configures runtime sbx"):
        h.place(_REPO.remote, {}, node="busy", runtime="sbx")


def test_the_hub_lists_each_nodes_runtimes(hub: tuple[Hub, _Clock]) -> None:
    h, _ = hub
    _node(h, "a", runtimes=["sbx", "gcp"])
    _node(h, "b")
    assert {view.name: view.runtimes for view in h.nodes()} == {"a": ["gcp", "sbx"], "b": []}
