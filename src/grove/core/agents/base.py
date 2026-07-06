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

from datetime import datetime
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

    ``resumable`` declares whether the tool can CONTINUE an existing session by
    id at launch (``launch_decoration(..., resume=True)`` yields a real handle) —
    the single source of truth the manager derives its resumable-kinds set from
    (#F10d), so a future resumable adapter can't be missed by a hand-maintained
    list. Claude Code / Codex are ``True``; a remote (mewbo) session and a bare
    shell have no launch resume handle, so ``False``.
    """

    kind: str
    remote: bool
    resumable: bool

    def launch_decoration(self, session_id: str, *, resume: bool = False) -> list[str]:
        """Extra argv tokens appended to the agent command so Grove owns the
        session id by construction (Claude Code → ``["--session-id", uuid]``).

        Empty for tools with no deterministic correlation handle — the generic
        shell adapter returns ``[]`` and Grove tracks nothing for it.

        ``resume=True`` asks the tool to CONTINUE an existing session rather than
        start a fresh one (#120): Claude Code → ``["--resume", id]`` (plain resume
        keeps the same session id/file), Codex → ``["resume", id]`` (a subcommand,
        valid after the command since Codex's grammar is
        ``codex [OPTIONS] <COMMAND> [ARGS]``). Kinds with no resume handle ignore
        the flag and stay empty — the manager gates resume to resumable kinds
        before ever calling this.
        """
        ...

    def model_decoration(self, model: str) -> list[str]:
        """Extra argv tokens that pin the model for this launch (Claude Code /
        Codex → ``["--model", model]``). Empty for tools with no launch-time
        model flag (mewbo selects server-side, the generic shell has no model),
        so they fall back to the tool's own default. The provider-boundary rule:
        Grove forwards the parameter as the tool's flag — it never interprets the
        value.
        """
        ...

    def available_models(self, command: str) -> tuple[str, ...]:
        """The model ids/aliases this tool advertises for its ``--model`` flag — a
        best-effort catalog a create-form picker OFFERS, never a validated
        allowlist (any id is still forwarded verbatim on create; the same
        provider boundary as :meth:`model_decoration`).

        ``command`` is the agent's configured launch command; an adapter that
        introspects a real CLI parses its binary from it (Codex runs ``<bin>
        debug models``). Read-only and best-effort like every adapter method:
        returns ``()`` when the tool exposes no catalog (a bare shell, a remote
        orchestrator whose models are server-side) or when none can be read.
        Adapters return their raw provider vocabulary — the engine
        (``registry.resolve_models``) de-dups and caps the offered list.
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

    def discover_births(
        self, cwd: Path, *, exclude_id: str | None = None
    ) -> list[tuple[str, datetime | None, float]]:
        """``(session_id, birth, mtime)`` for sessions in ``cwd``, newest-first by mtime.

        The CHEAP pre-filter behind the dashboard's adoption gate (#F5): the same
        scan as :meth:`discover_sessions`, but each id paired with its session
        BIRTH (first-record timestamp, from the bounded head read the scan
        already does — never a full transcript parse) and the transcript mtime
        (for newest-first ordering when a caller unions several cwds, #F7). The
        service evaluates the birth-or-live-here gate on this cheap metadata
        FIRST and pays a full activity parse only for candidates that pass, so
        per-tick cost is O(new sessions), not O(history). ``birth`` is ``None``
        for a transcript with no timestamped record. Best-effort: ``[]`` on error
        or for tools with no discoverable transcripts (generic, remote).
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
