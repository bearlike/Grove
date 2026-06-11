"""The :class:`AgentAdapter` contract — how Grove introspects one agent tool.

A ``Protocol`` rather than an ABC because the codebase's convention for "one
contract, several transport-style implementations" is structural typing (see the
``AttachSession`` Protocol lesson in CLAUDE.md). Two real implementations exist
— ``ClaudeCodeAdapter`` and ``GenericAdapter`` — which is exactly the bar for
introducing the abstraction at all (CLAUDE.md: protocols only when more than one
real implementation exists).

Every method is read-only over the filesystem or pure logic; adapters hold no
mutable state. The launch decoration is the *only* outward-facing method — it
feeds argv into ``tmux.build_workspace_layout`` (#13) — and even that returns a
plain token list, leaving the side effect to ``tmux.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from grove.core.agents.model import (
    AgentActivity,
    OrderedDigest,
    SessionSummary,
    SessionTurn,
)


class AgentAdapter(Protocol):
    """Tool-specific introspection behind a tool-agnostic surface.

    ``kind`` is the class-level discriminator that matches ``AgentSpec.kind`` in
    config; ``registry.get_adapter(kind)`` selects the implementation.
    """

    kind: str

    def launch_decoration(self, session_id: str) -> list[str]:
        """Extra argv tokens appended to the agent command so Grove owns the
        session id by construction (Claude Code → ``["--session-id", uuid]``).

        Empty for tools with no deterministic correlation handle — the generic
        shell adapter returns ``[]`` and Grove tracks nothing for it.
        """
        ...

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]:
        """Every transcript file for ``session_id`` (main thread first, then any
        sub-agent files), resolved under the agent's config dir for a session
        whose working directory is ``cwd``.

        Read-only. Returns ``[]`` — never raises — when nothing is on disk yet
        (the STARTING window) or the tool isn't Claude Code.
        """
        ...

    def discover_sessions(self, cwd: Path, *, exclude_id: str | None = None) -> list[str]:
        """Session ids the tool ran in ``cwd`` that Grove didn't launch (#18).

        Out-of-band discovery: surfaces sessions a user started by hand in a Grove
        worktree. Read-only, best-effort (returns ``[]`` on error or when the tool
        has no discoverable transcripts). ``exclude_id`` drops the Grove-launched
        session so only the hand-started ones remain.
        """
        ...

    def list_sessions(self, cwd: Path) -> list[SessionSummary]:
        """Every session the tool recorded for ``cwd``, newest-first by mtime.

        The session-exploration analogue of ``discover_sessions`` — same
        read-only scan, but returning the normalized listing metadata (plus a
        point-in-time activity parse) instead of bare ids, and *without* an
        exclusion: Grove-launched and hand-started sessions both appear.
        Best-effort: ``[]`` on error or for tools with no transcripts.
        """
        ...

    def read_turns(
        self, paths: Sequence[Path], *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """The normalized conversation in ``paths``, oldest turn first.

        ``last`` keeps only the most recent N turns (the `sessions show
        --last` window). Best-effort like ``parse_activity``: corrupt lines
        are skipped, empty ``paths`` yields ``()``.
        """
        ...

    def parse_activity(self, paths: Sequence[Path]) -> AgentActivity:
        """Normalized activity from the transcript file(s).

        Best-effort by contract (the peek rule): a corrupt line, an unknown
        record type, or a vanished file degrades the result rather than raising.
        Empty ``paths`` yields an ``UNKNOWN`` activity — the STARTING vs UNKNOWN
        distinction is the ``ActivityService``'s call, since only it knows
        whether a session id was ever minted.
        """
        ...

    def transcript_digest(self, paths: Sequence[Path]) -> OrderedDigest:
        """Compact ordered slice for the future external-LLM interpreter (#20).

        Minimal in the MVP; the seam exists so #20 never has to reshape the
        adapter contract.
        """
        ...
