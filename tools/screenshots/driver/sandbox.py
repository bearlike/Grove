"""The throwaway tree every capture run lives inside.

IMPORTS NO GROVE MODULE, AND MUST NOT. `platformdirs` and both agent adapters
resolve their directories from the environment at CALL time, but a capture has
to redirect that environment BEFORE the first grove import so nothing resolves a
real path even once. That ordering is the reason this module is separate, and
the reason `tools/screenshots/__init__.py` is empty: importing an entrypoint
executes its package first, so anything re-exported there would run before the
entrypoint's own `Sandbox(...).activate()` line.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class Sandbox:
    """A directory tree plus the environment that points every lookup at it.

    ``CLAUDE_CONFIG_DIR`` alone is not enough: `_ClaudeHome.config_dirs()`
    ALWAYS appends ``~/.config/claude`` and ``~/.claude`` as extra fallback
    candidates on top of it (a real, intentional cascade — see
    ``src/grove/core/agents/claude_code.py``), so a host-wide scan such as the
    usage projector's still walks the real profile. Redirecting ``HOME`` closes
    every `Path.home()`-relative fallback in one move, Codex's own ``~/.codex``
    default included.
    """

    root: Path

    DIRS: ClassVar[tuple[tuple[str, str], ...]] = (
        ("HOME", "home"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_STATE_HOME", "state"),
        ("CLAUDE_CONFIG_DIR", "claude"),
        ("CODEX_HOME", "codex"),
    )

    @property
    def fleet(self) -> Path:
        """Where the demo repos, worktrees and workspace store live."""
        return self.root / "fleet"

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def activate(self) -> None:
        """Export the redirected environment. Call before importing grove."""
        for name, child in self.DIRS:
            os.environ[name] = str(self.root / child)

    @property
    def seeded(self) -> bool:
        return self.fleet.exists()

    def reset(self) -> None:
        """Discard whatever is there and start from an empty tree."""
        self.discard()
        self.root.mkdir(parents=True)

    def discard(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)
