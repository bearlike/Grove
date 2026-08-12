"""Throwaway git repositories, and the two edits that make their stats non-trivial."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GitTree:
    """A directory git can be run in — a demo repo, or one of its worktrees."""

    path: Path

    @classmethod
    def init_repo(cls, parent: Path, name: str) -> GitTree:
        """A fresh repo on branch ``current`` with one commit, under ``parent``."""
        repo = parent / name
        repo.mkdir(parents=True)
        tree = cls(path=repo)
        tree.run("init", "-b", "current")
        tree.run("config", "user.email", "demo@grove.local")
        tree.run("config", "user.name", "Grove Demo")
        (repo / "README.md").write_text(f"# {name}\n\nDemo repository.\n", encoding="utf-8")
        tree.run("add", ".")
        tree.run("commit", "-m", "init", "--no-verify")
        return cls(path=repo.resolve())

    def run(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.path, check=True, capture_output=True)

    def leave_dirty(self) -> None:
        """One uncommitted edit, so the peek summary shows a dirty count."""
        readme = self.path / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "\nWIP.\n", encoding="utf-8")

    def commit_ahead(self, name: str) -> None:
        """One commit on the branch, so the summary shows ``ahead 1``."""
        (self.path / f"{name}.txt").write_text("scratch\n", encoding="utf-8")
        self.run("add", ".")
        self.run("commit", "-m", f"wip: {name}", "--no-verify")
