"""CLI and MCP discover the same installed workflows without daemon authority."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from grove._skills import SkillLibrary
from grove.mcp.tools import GroveTools
from grove.tui.cli_onboarding import skills_app


def test_catalog_describes_every_installed_skill() -> None:
    catalog = SkillLibrary.catalog()
    assert tuple(skill.name for skill in catalog) == SkillLibrary.names()
    for skill in catalog:
        assert skill.description in SkillLibrary.read(skill.name)
        assert skill.cli == f"grove skills show {skill.name}"
        assert skill.resource == f"grove://skills/{skill.name}"


def test_cli_preserves_names_and_offers_details() -> None:
    runner = CliRunner()
    plain = runner.invoke(skills_app, ["list"])
    assert plain.exit_code == 0
    assert tuple(plain.stdout.splitlines()) == SkillLibrary.names()
    detailed = runner.invoke(skills_app, ["list", "--details"])
    assert detailed.exit_code == 0
    for skill in SkillLibrary.catalog():
        assert skill.description in detailed.stdout
        assert skill.cli in detailed.stdout
        assert skill.resource in detailed.stdout
        assert runner.invoke(skills_app, ["show", skill.name]).stdout == SkillLibrary.read(
            skill.name
        )


@pytest.mark.asyncio
async def test_mcp_catalog_and_full_reads_need_no_client() -> None:
    tools = GroveTools(None)  # type: ignore[arg-type] — help must never touch the client
    assert await tools.get_skill() == SkillLibrary.names()
    assert await tools.get_skill(details=True) == SkillLibrary.catalog()
    for name in SkillLibrary.names():
        assert await tools.get_skill(name, details=True) == SkillLibrary.read(name)


def test_catalog_rejects_invalid_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "skills"
    skill = root / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: wrong\ndescription: Example workflow\n---\nBody")
    monkeypatch.setattr(SkillLibrary, "root", classmethod(lambda cls: root))
    with pytest.raises(ValueError, match="name and description"):
        SkillLibrary.catalog()
