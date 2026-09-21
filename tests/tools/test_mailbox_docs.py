"""The published mailbox guidance says what the code actually does.

Nothing else reads prose, so these are the only detector for a skill or docs
page that still describes the enrollment design the code no longer has.
"""

from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS = (
    ROOT / "docs" / "configure-agents.md",
    ROOT / "docs" / "use-mcp.md",
    ROOT / "docs" / "use-cli.md",
)
SKILLS = (
    ROOT / "src" / "grove" / "skills" / "using-grove" / "SKILL.md",
    ROOT / "src" / "grove" / "skills" / "working-in-grove" / "SKILL.md",
    ROOT / "src" / "grove" / "skills" / "configuring-grove" / "SKILL.md",
)

# Every vocabulary item the simplification removed. A doc naming one of these
# is describing a mechanism no longer in the code.
RETIRED = (
    "--mailbox-only",
    "GROVE_MCP_MAILBOX_ONLY",
    "GROVE_MAILBOX_TOKEN",
    "grove mailbox peers",
    "grove mailbox reply",
    "grove mailbox status",
    "--generation",
    "expected_generation",
    "grove_list_mailbox_peers",
    "grove_get_mailbox_message_status",
)


def text(paths: tuple[Path, ...]) -> str:
    return "\n".join(path.read_text() for path in paths)


def test_the_guidance_names_no_retired_mechanism() -> None:
    guidance = text(DOCS + SKILLS)

    named = [term for term in RETIRED if term in guidance]

    assert named == [], f"guidance still describes removed mechanisms: {named}"


def test_the_guidance_teaches_contacts_and_addressed_send() -> None:
    guidance = text(DOCS + SKILLS)

    assert "grove mailbox contacts" in guidance
    assert "grove mailbox send" in guidance
    assert "grove_list_mailbox_contacts" in guidance
    assert "grove_send_mailbox_message" in guidance


def test_the_guidance_says_a_terminal_session_can_be_written_to() -> None:
    """The user-visible half of the change: no agent is excluded."""
    guidance = text(DOCS + SKILLS).lower()

    assert "interactive terminal" in guidance
    assert "ordinary recipients" in guidance or "ordinary mailbox contact" in guidance


def test_the_guidance_keeps_the_trust_and_receipt_limits() -> None:
    """Simplifying participation must not soften what a receipt claims."""
    guidance = text(DOCS + SKILLS).lower()

    assert "untrusted" in guidance
    assert "never grants" in guidance or "never changes a permission" in guidance
    assert "never that a model read" in guidance or "never that the other model acted" in guidance


def test_the_documented_examples_match_the_real_cli_flags() -> None:
    """A published example that does not parse is worse than none."""
    cli = (ROOT / "src" / "grove" / "tui" / "cli_mailbox.py").read_text()
    working_skill = (
        ROOT / "src" / "grove" / "skills" / "working-in-grove" / "SKILL.md"
    ).read_text()

    for flag in ("--to", "--subject", "--body", "--from", "--to-agent", "--in-reply-to"):
        assert f'"{flag}"' in cli, f"{flag} is documented but not a real CLI flag"
    assert 'grove mailbox send --to <workspace-id> --subject "Ready for review"' in working_skill
