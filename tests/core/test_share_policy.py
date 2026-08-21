"""Per-project public share policy persists only hashes and TTLs."""

from __future__ import annotations

import json
from pathlib import Path

from grove.core.share_policy import SharePolicy, SharePolicyStore


def test_policy_store_round_trip_through_a_fresh_instance(tmp_path: Path) -> None:
    path = tmp_path / "share-policies.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    store = SharePolicyStore(path)

    saved = store.save(SharePolicy.for_repo(repo, passcode="open-sesame", ttl_seconds=600))
    reloaded = SharePolicyStore(path).get(repo)

    assert saved == reloaded
    assert reloaded.passcode_hash != "open-sesame"
    assert reloaded.verifies("open-sesame")
    assert not reloaded.verifies("wrong")


def test_legacy_policy_row_without_protections_loads_unprotected(tmp_path: Path) -> None:
    path = tmp_path / "share-policies.json"
    repo = tmp_path / "repo"
    repo.mkdir()
    path.write_text(
        json.dumps({"version": 1, "policies": {str(repo.resolve()): {}}}),
        encoding="utf-8",
    )

    policy = SharePolicyStore(path).get(repo)

    assert policy.passcode_hash is None
    assert policy.ttl_seconds is None
    assert policy.verifies(None)
