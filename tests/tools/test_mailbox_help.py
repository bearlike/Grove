"""Bundled help points mailbox workers at the registered public surfaces."""

from __future__ import annotations

import json
from pathlib import Path

from grove._skills import SkillLibrary
from grove.mcp.instructions import SERVER_INSTRUCTIONS

ROOT = Path(__file__).parents[2]


def test_mailbox_help_teaches_the_whole_surface_from_a_cold_start() -> None:
    """An agent reading only the bundled help must be able to write to a peer."""
    using = SkillLibrary.read("using-grove")
    working = SkillLibrary.read("working-in-grove")
    configuring = SkillLibrary.read("configuring-grove")

    assert "mailbox" in using.lower()
    assert "mailbox" in configuring.lower()
    # The terminal twin is the documented alternative to the (default) native session.
    assert '"native": false' in configuring
    assert "claude-terminal" in configuring
    # Both roads to a message, named where an agent will look for them.
    assert "grove mailbox contacts" in working
    assert "grove mailbox send" in working
    assert "grove_list_mailbox_contacts" in working
    assert "grove_send_mailbox_message" in working
    assert "grove_list_mailbox_contacts" in SERVER_INSTRUCTIONS
    assert "grove_send_mailbox_message" in SERVER_INSTRUCTIONS


def test_the_server_instructions_state_the_receipt_and_trust_limits() -> None:
    """The tool descriptions are what most agents read instead of a skill."""
    assert "never that a model read" in SERVER_INSTRUCTIONS
    assert "never consent" in SERVER_INSTRUCTIONS
    assert "interactive terminal" in SERVER_INSTRUCTIONS


def test_marketplace_description_does_not_publish_a_stale_skill_count() -> None:
    """Skill discovery comes from the packaged roster, not marketplace prose."""
    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    description = marketplace["plugins"][0]["description"]

    assert "skills:" not in description.lower()
    assert "four skills" not in description.lower()
