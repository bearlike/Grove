"""Read Grove's bundled skill package data without depending on the engine."""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict


class SkillSummary(BaseModel):
    """Installed workflow discovery; full instructions remain an on-demand read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    cli: str
    resource: str


class SkillLibrary:
    """The installed bundled-skill roster and its exact source text."""

    PACKAGE_DIR: ClassVar[str] = "skills"
    SKILL_FILE: ClassVar[str] = "SKILL.md"

    @classmethod
    def root(cls) -> Path:
        """Return the installed package directory which is the skill roster."""
        return Path(str(importlib.resources.files("grove") / cls.PACKAGE_DIR))

    @classmethod
    def dirs(cls) -> tuple[Path, ...]:
        """Return real skill directories with a source file in stable name order."""
        directories = (
            path
            for path in cls.root().iterdir()
            if path.is_dir() and not path.is_symlink() and (path / cls.SKILL_FILE).is_file()
        )
        return tuple(sorted(directories, key=lambda path: path.name))

    @classmethod
    def names(cls) -> tuple[str, ...]:
        """Return the exact names accepted by :meth:`read`."""
        return tuple(path.name for path in cls.dirs())

    @classmethod
    def catalog(cls) -> tuple[SkillSummary, ...]:
        """Describe every installed skill from its own package-owned frontmatter.

        Bundled metadata uses single-line plain scalars. Reject unsupported
        formatting rather than silently publish an empty or misleading catalog.
        This reads our package data, not arbitrary user-authored YAML.
        """
        summaries = []
        for name in cls.names():
            lines = cls.read(name).splitlines()
            if not lines or lines[0] != "---" or "---" not in lines[1:]:
                raise ValueError(f"bundled skill {name!r} has no frontmatter")
            header = lines[1 : lines.index("---", 1)]
            fields = dict(line.split(": ", 1) for line in header if ": " in line)
            description = fields.get("description", "").strip()
            if fields.get("name") != name or not description or description[0] in "|>\"'":
                raise ValueError(
                    f"bundled skill {name!r} needs plain name and description metadata"
                )
            summaries.append(
                SkillSummary(
                    name=name,
                    description=description,
                    cli=f"grove skills show {name}",
                    resource=f"grove://skills/{name}",
                )
            )
        return tuple(summaries)

    @classmethod
    def read(cls, name: str) -> str:
        """Return one complete bundled skill, rejecting every unadvertised name."""
        names = cls.names()
        if name not in names:
            available = ", ".join(names)
            raise ValueError(f"unknown bundled skill {name!r}; available: {available}")
        return (cls.root() / name / cls.SKILL_FILE).read_text(encoding="utf-8")


__all__ = ["SkillLibrary"]
