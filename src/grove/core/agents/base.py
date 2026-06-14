"""The :class:`AgentAdapter` contract — how Grove introspects one agent tool.

A ``Protocol`` rather than an ABC because the codebase's convention for "one
contract, several transport-style implementations" is structural typing (see the
``AttachSession`` Protocol lesson in CLAUDE.md). Two real implementations exist
— ``ClaudeCodeAdapter`` and ``GenericAdapter`` — which is exactly the bar for
introducing the abstraction at all (CLAUDE.md: protocols only when more than one
real implementation exists).

Every method is read-only over the filesystem (or a remote API) or pure logic;
adapters hold no mutable state. The launch decoration is the *only*
outward-facing method — it feeds argv into ``tmux.build_workspace_layout``
(#13) — and even that returns a plain token list, leaving the side effect to
``tmux.py``.

The session-reading unit of reference is ``(cwd, session_id)``, never a file
path: how a session id resolves to backing storage (a transcript glob, an HTTP
endpoint) is each adapter's internal detail, so a remote adapter (#36) fits the
seam without faking filesystem ``Path``s. ``locate_transcripts`` is the one
deliberately filesystem-shaped method, kept for callers that genuinely want
the files (dump, transcript-path display).
"""

from __future__ import annotations

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

    ``remote`` declares where the session actually runs: ``True`` means the work
    happens on a backend service and the local tmux pane says nothing about it —
    the blend must trust the adapter's reported state instead of demoting a
    quiet pane to IDLE. Filesystem adapters are ``False``.
    """

    kind: str
    remote: bool

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

        The deliberately filesystem-shaped surface: filesystem adapters return
        the backing files; remote adapters return ``[]`` — their sessions have
        no local file, and callers wanting session *content* use the
        ``(cwd, session_id)``-keyed readers below instead. Read-only. Returns
        ``[]`` — never raises — when nothing is on disk yet (the STARTING
        window) or the tool keeps no transcripts.
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
        self, cwd: Path, session_id: str, *, last: int | None = None
    ) -> tuple[SessionTurn, ...]:
        """The normalized conversation for ``session_id`` in ``cwd``, oldest
        turn first.

        ``last`` keeps only the most recent N turns (the `sessions show
        --last` window). Best-effort like ``parse_activity``: corrupt records
        are skipped, an empty or missing session yields ``()``.
        """
        ...

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity:
        """Normalized activity for ``session_id`` whose working directory is ``cwd``.

        Best-effort by contract (the peek rule): a corrupt record, an unknown
        record type, or a vanished backing store degrades the result rather
        than raising. An empty or missing session yields an ``UNKNOWN``
        activity — the STARTING vs UNKNOWN distinction is the
        ``ActivityService``'s call, since only it knows whether a session id
        was ever minted.
        """
        ...

    def transcript_digest(self, cwd: Path, session_id: str) -> OrderedDigest:
        """Compact ordered slice for the future external-LLM interpreter (#20).

        Minimal in the MVP; the seam exists so #20 never has to reshape the
        adapter contract. Best-effort: an empty or missing session yields an
        empty digest.
        """
        ...
