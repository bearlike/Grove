"""Regression checks for the public mailbox guidance."""

from pathlib import Path

ROOT = Path(__file__).parents[2]
DOCS = (
    ROOT / "docs" / "configure-agents.md",
    ROOT / "docs" / "use-mcp.md",
)
SKILLS = (
    ROOT / "src" / "grove" / "skills" / "using-grove" / "SKILL.md",
    ROOT / "src" / "grove" / "skills" / "working-in-grove" / "SKILL.md",
    ROOT / "src" / "grove" / "skills" / "configuring-grove" / "SKILL.md",
)


def text(paths: tuple[Path, ...]) -> str:
    return "\n".join(path.read_text() for path in paths)


def test_mailbox_guidance_names_native_worker_contract() -> None:
    guidance = text(DOCS + SKILLS)

    # Native by default since S2: the guidance says what a native session IS,
    # that it never attaches to a running TUI, and that it cannot be resumed.
    assert "Grove-owned native session" in guidance
    assert "not an attach mechanism" in guidance
    assert "not resumable" in guidance


def test_mailbox_guidance_names_token_and_primary_limit() -> None:
    guidance = text(DOCS + SKILLS)

    assert "GROVE_MAILBOX_TOKEN" in guidance
    assert "primary agent" in guidance
    assert "--mailbox-only" in guidance


def test_mailbox_guidance_does_not_claim_containers_are_unverified() -> None:
    guidance = text(DOCS + SKILLS)

    assert "not yet verified end to end" not in guidance
    assert "cannot reach Grove at all" not in guidance


def test_mailbox_guidance_explains_default_mcp_scope() -> None:
    guidance = text(DOCS + SKILLS)

    assert "does not register mailbox tools" in guidance
    assert "--mailbox-only" in guidance
    assert "default native tool permission" in guidance


def test_mailbox_examples_match_cli_and_mcp_schemas() -> None:
    mailbox_cli = (ROOT / "src" / "grove" / "tui" / "cli_mailbox.py").read_text()
    mcp_server = (ROOT / "src" / "grove" / "mcp" / "server.py").read_text()
    working_skill = SKILLS[1].read_text()

    assert '"--generation", help="Recipient generation from mailbox peers."' in mailbox_cli
    assert '"--agent", help="Recipient agent slot."' in mailbox_cli
    assert "--mailbox-only" in mcp_server
    example = "grove mailbox send <workspace-id> --agent <slot> --generation <generation>"
    assert example in working_skill
