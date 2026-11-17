"""`grove federation check` dry-runs the block the daemon would load."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from grove.core import paths
from grove.tui.cli import app


def _user_config(body: dict[str, object]) -> None:
    path = paths.user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body))


def test_check_fails_with_the_reason_an_enabled_block_cannot_run(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    _user_config({"federation": {"enabled": True, "node": {"role": "node"}}})
    result = CliRunner().invoke(app, ["federation", "check"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"].startswith("disabled (federation.node.hub_url")


def test_check_passes_for_a_hub(tmp_state_dir: Path) -> None:
    del tmp_state_dir
    _user_config({"federation": {"enabled": True, "node": {"role": "hub"}}})
    result = CliRunner().invoke(app, ["federation", "check"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["status"] == "hub"


def test_node_join_refuses_a_hub_other_than_the_configured_one(tmp_state_dir: Path) -> None:
    """Joining writes a key for ONE hub; a typo must not mint a key nobody configured."""
    del tmp_state_dir
    _user_config(
        {
            "federation": {
                "enabled": True,
                "node": {"role": "node", "hub_url": "http://hub-a", "advertise_url": "http://n"},
            }
        }
    )
    result = CliRunner().invoke(app, ["node", "join", "http://hub-b", "--token", "t"])
    assert result.exit_code == 1
    assert "configured for hub 'http://hub-a'" in result.output
