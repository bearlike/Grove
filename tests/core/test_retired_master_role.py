"""State an older build wrote for the retired master role still loads.

A host upgraded from a build that ran master sessions holds three things the
current code no longer models: a ``role`` on a workspace record, a ``supervise``
row in the watch log, and a ``master`` section in a config file. Each must load
rather than fail the daemon's start, and the config's one value must survive.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from grove.core.config import load_config
from grove.core.contracts.mailboxes import MailboxAddress
from grove.core.contracts.watches import TriggerPredicate, WatchView
from grove.core.store import JsonWorkspaceStore
from grove.core.watches.log import WatchLog
from grove.core.workspace import WorkspaceState, WorkspaceStatus

WS = "a" * 32
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def test_a_record_written_with_a_master_role_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = JsonWorkspaceStore(path)
    store.save(
        WorkspaceState(
            id=WS,
            title="fleet",
            repo_root=str(tmp_path),
            branch="main",
            base_branch="HEAD",
            worktree_path=str(tmp_path),
            tmux_session="grove-fleet",
            agent_name="claude",
            status=WorkspaceStatus.RUNNING,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    raw = json.loads(path.read_text())
    raw["workspaces"][WS]["role"] = "master"
    path.write_text(json.dumps(raw))

    assert JsonWorkspaceStore(path).get(WS).title == "fleet"


def test_a_retired_supervise_row_is_dropped_not_held_aside(tmp_path: Path) -> None:
    path = tmp_path / "watches.json"
    retired = {
        "id": "wch_" + "1" * 32,
        "recipient": {"workspace_id": WS, "agent": ""},
        "predicate": {"kind": "supervise", "workspace_id": WS, "acknowledged": {}, "pending": {}},
        "state": "pending",
        "created_at": NOW.isoformat(),
        "expires_at": None,
    }
    path.write_text(json.dumps({"version": 1, "watches": {retired["id"]: retired}}))
    log = WatchLog(path)

    assert log.active() == []
    log.put(
        WatchView(
            id="wch_" + "2" * 32,
            recipient=MailboxAddress(workspace_id=WS),
            predicate=TriggerPredicate(workspace_id=WS),
            state="pending",
            created_at=NOW,
            expires_at=None,
        )
    )
    assert "supervise" not in path.read_text()


def test_a_master_section_moves_its_quiet_minutes_onto_the_fleet_digest(
    tmp_state_dir: Path,
) -> None:
    del tmp_state_dir
    cfg = load_config(None, env={}, cli_overrides={"master": {"quiet_minutes": 12}})
    assert cfg.fleet_digest.quiet_minutes == 12


def test_a_fleet_digest_value_in_the_same_layer_wins_over_the_retired_one(
    tmp_state_dir: Path,
) -> None:
    del tmp_state_dir
    cfg = load_config(
        None,
        env={},
        cli_overrides={"master": {"quiet_minutes": 12}, "fleet_digest": {"quiet_minutes": 45}},
    )
    assert cfg.fleet_digest.quiet_minutes == 45
