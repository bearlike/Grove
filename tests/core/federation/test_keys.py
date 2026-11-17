"""The key two daemons share: single-use join, signed bearers, revocation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.federation import NodeAuthRefused, NodeBearer, NodeCatalogFile, NodeKey


def test_a_join_token_admits_exactly_one_node_and_is_never_stored(tmp_path: Path) -> None:
    catalog = NodeCatalogFile(tmp_path / "nodes.json")
    token = catalog.mint_join_token(timedelta(minutes=5))
    node, secret = catalog.admit(token, name="a", advertise_url="http://a")
    assert token not in (tmp_path / "nodes.json").read_text()
    assert secret not in (tmp_path / "nodes.json").read_text()
    assert (tmp_path / "nodes.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(NodeAuthRefused, match="already used"):
        catalog.admit(token, name="b", advertise_url="http://b")
    assert [n.node_id for n in catalog.nodes()] == [node.node_id]


def test_an_expired_join_token_is_refused(tmp_path: Path) -> None:
    now = [datetime(2026, 10, 21, tzinfo=UTC)]
    catalog = NodeCatalogFile(tmp_path / "nodes.json", clock=lambda: now[0])
    token = catalog.mint_join_token(timedelta(minutes=5))
    now[0] += timedelta(minutes=6)
    with pytest.raises(NodeAuthRefused, match="expired"):
        catalog.admit(token, name="a", advertise_url="http://a")


def test_both_sides_derive_the_same_signing_key_and_the_audience_is_bound(
    tmp_path: Path,
) -> None:
    catalog = NodeCatalogFile(tmp_path / "nodes.json")
    node, secret = catalog.admit(
        catalog.mint_join_token(timedelta(minutes=5)), name="a", advertise_url="http://a"
    )
    key = NodeKey(tmp_path / "node-key")
    key.store(node.node_id, secret)
    assert (tmp_path / "node-key").stat().st_mode & 0o777 == 0o600
    node_id, signing_key = key.load() or ("", "")
    assert signing_key == node.signing_key

    to_hub = NodeBearer.sign(node_id, signing_key, audience="hub")
    assert NodeBearer.verify(to_hub, node.signing_key, audience="hub", max_skew_seconds=60)
    # A bearer the node sent the hub cannot be replayed at the node as the hub's.
    with pytest.raises(NodeAuthRefused, match="signature"):
        NodeBearer.verify(to_hub, signing_key, audience="node", max_skew_seconds=60)
    stale = NodeBearer.sign(node_id, signing_key, audience="hub", now=0)
    with pytest.raises(NodeAuthRefused, match="skew"):
        NodeBearer.verify(stale, node.signing_key, audience="hub", max_skew_seconds=60)
    with pytest.raises(NodeAuthRefused, match="signature"):
        NodeBearer.verify(
            NodeBearer.sign(node_id, "not-the-key", audience="hub"),
            node.signing_key,
            audience="hub",
            max_skew_seconds=60,
        )
