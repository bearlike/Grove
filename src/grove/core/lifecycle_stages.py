"""A lifecycle verb as a recorded list of stages, so a process that dies mid-verb can be finished.

A verb like ``create`` or ``kill`` is several side effects in a row (a record,
a worktree, a container, a tmux session). Each stage is written to a small
per-workspace progress file the moment it is ENTERED, before its side effect
runs, and the verb holds that file's lock for its whole run. Two consequences:

* a failure unwinds exactly the stages that began, in reverse, and reports the
  ones whose inverse failed as residue (the shape ``kill`` already reports);
* a progress file whose lock is free belongs to a verb whose process is gone
  (a kernel ``flock`` dies with its holder), so daemon start can finish it.

Entered, not completed, is deliberate: ``git worktree add -b`` creates its ref
before it can fail, so a stage that raised has still left something to undo.
Every inverse is best-effort and safe to run against nothing.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from loguru import logger

from grove.core import paths

#: The verbs that record their stages.
LifecycleVerb = Literal["create", "kill"]


@dataclass(slots=True)
class Stages:
    """The stages one running verb has entered, mirrored to its progress file."""

    verb: LifecycleVerb
    path: Path
    entered: list[str] = field(default_factory=list)

    def enter(self, stage: str) -> None:
        """Record *stage* durably before its side effect runs."""
        self.entered.append(stage)
        paths.write_atomic(self.path, json.dumps({"verb": self.verb, "entered": self.entered}))

    def unwind(self, inverses: Mapping[str, Callable[[], None]]) -> Sequence[str]:
        """Run the inverse of every entered stage, newest first.

        Args:
            inverses: the undo for each stage name; a stage with none is skipped.

        Returns:
            The stages whose inverse raised, oldest first; empty when all undid cleanly.
        """
        residue: list[str] = []
        for stage in reversed(self.entered):
            undo = inverses.get(stage)
            if undo is None:
                continue
            try:
                undo()
            except Exception as exc:
                logger.warning("{} rollback: {} not undone: {}", self.verb, stage, exc)
                residue.insert(0, stage)
        return residue

    @staticmethod
    def path_for(workspace_id: str) -> Path:
        """Where one workspace's in-flight verb records its stages."""
        return paths.lifecycle_dir() / f"{workspace_id}.json"

    @classmethod
    @contextmanager
    def run(cls, verb: LifecycleVerb, workspace_id: str) -> Iterator[Stages]:
        """Hold the workspace's progress lock for one verb; drop the file when it returns.

        The file is removed on every exit, raised or not, because a verb that
        returned has already reported or unwound what it did. Only a process
        that dies leaves one behind, which is exactly what start-up looks for.
        """
        stages = cls(verb, cls.path_for(workspace_id))
        with paths.exclusive_lock(stages.path):
            try:
                yield stages
            finally:
                stages.path.unlink(missing_ok=True)

    @classmethod
    def interrupted(cls) -> list[tuple[str, Stages]]:
        """Every verb whose process died mid-run, by workspace id.

        Returns:
            One entry per progress file whose lock is free; empty when none.
            An unreadable file is reported and skipped, never guessed at.
        """
        found: list[tuple[str, Stages]] = []
        directory = paths.lifecycle_dir()
        if not directory.is_dir():
            return found
        for path in sorted(directory.glob("*.json")):
            if paths.lock_is_held(path):
                continue
            try:
                data = json.loads(path.read_text())
                stages = cls(data["verb"], path, list(data["entered"]))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                logger.warning("lifecycle: unreadable progress file {}: {}", path, exc)
                continue
            found.append((path.stem, stages))
        return found


__all__ = ["LifecycleVerb", "Stages"]
