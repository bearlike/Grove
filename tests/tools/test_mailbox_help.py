"""Bundled help points mailbox workers at the registered public surfaces."""

from __future__ import annotations

import json
from pathlib import Path

from grove._skills import SkillLibrary
from grove.mcp.instructions import SERVER_INSTRUCTIONS

ROOT = Path(__file__).parents[2]


def test_mailbox_help_covers_the_owned_native_worker_and_mail_contract() -> None:
    """The cold-start help must name the only supported mailbox control plane."""
    using = SkillLibrary.read("using-grove")
    working = SkillLibrary.read("working-in-grove")
    configuring = SkillLibrary.read("configuring-grove")

    assert "mailbox" in using.lower()
    assert "mailbox" in working.lower()
    assert "mailbox" in configuring.lower()
    # The terminal twin is the documented alternative to the (default) native session.
    assert '"native": false' in configuring
    assert "claude-terminal" in configuring
    assert "GROVE_MAILBOX_TOKEN" in working
    assert "grove mailbox peers" in working
    assert "grove mailbox send" in working
    assert "grove mailbox reply" in working
    assert "grove mailbox status" in working
    assert "grove_list_mailbox_peers" in working
    assert "grove_send_mailbox_message" in working
    assert "grove_get_mailbox_message_status" in working
    assert "grove_list_mailbox_peers" in SERVER_INSTRUCTIONS
    assert "grove_send_mailbox_message" in SERVER_INSTRUCTIONS
    assert "grove_get_mailbox_message_status" in SERVER_INSTRUCTIONS


def test_marketplace_description_does_not_publish_a_stale_skill_count() -> None:
    """Skill discovery comes from the packaged roster, not marketplace prose."""
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    description = marketplace["plugins"][0]["description"]

    assert "skills:" not in description.lower()
    assert "four skills" not in description.lower()
