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
— and even that returns a plain token list, leaving the side effect to
``tmux.py``.

The session-reading unit of reference is ``(cwd, session_id)``, never a file
path: how a session id resolves to backing storage (a transcript glob, an HTTP
endpoint) is each adapter's internal detail, so a remote adapter fits the
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
    FinalResult,
    OrderedDigest,
    SessionControls,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TodoList,
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
    the single source of truth the manager derives its resumable-kinds set from,
    so a future resumable adapter can't be missed by a hand-maintained
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
        start a fresh one: Claude Code → ``["--resume", id]`` (plain resume
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

    def offline_decoration(self) -> list[str]:
        """Extra argv tokens that disallow network-facing tools for this launch
        (Claude Code → ``["--disallowedTools", "WebFetch,WebSearch"]``,
        Codex → its workspace-write-no-network sandbox flags). Empty for tools
        with no local network-tool gate (mewbo runs server-side, the generic
        shell has no tool concept). Gated by the caller on `AgentSpec.tools_offline`
        — the provider boundary: Grove forwards the tool's own flag shape, it
        never re-derives which tools are "network" from behavior.
        """
        ...

    def telemetry_env(self) -> dict[str, str]:
        """The tool's OWN native-telemetry *enable* env vars.

        The provider-boundary sibling of :meth:`offline_decoration`: the manager
        already injects the generic OTLP endpoint/headers (``TelemetryConfig.
        derive_env``), but *turning the tool's native exporter on* is a
        provider-specific flag the adapter owns — Claude Code →
        ``{"CLAUDE_CODE_ENABLE_TELEMETRY": "1", "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORTER": "otlp"}`` (its usage/cost/tool metrics + api_request
        events stream to whatever OTLP endpoint the env already carries). Empty
        for a tool with no env-driven telemetry switch (Codex configures OTel
        through ``config.toml [otel]``, mewbo is server-side, the generic shell
        emits nothing). Gated by the caller on telemetry being enabled AND the
        endpoint actually resolving, so the tool never enables an exporter with
        nowhere to send. Grove forwards the tool's own flag names — it never
        invents telemetry semantics (the same boundary as ``offline_decoration``).
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
        """Session ids the tool ran in ``cwd`` that Grove didn't launch.

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

        The CHEAP pre-filter behind the dashboard's adoption gate: the same
        scan as :meth:`discover_sessions`, but each id paired with its session
        BIRTH (first-record timestamp, from the bounded head read the scan
        already does — never a full transcript parse) and the transcript mtime
        (for newest-first ordering when a caller unions several cwds). The
        service evaluates the birth-or-live-here gate on this cheap metadata
        FIRST and pays a full activity parse only for candidates that pass, so
        per-tick cost is O(new sessions), not O(history). ``birth`` is ``None``
        for a transcript with no timestamped record. Best-effort: ``[]`` on error
        or for tools with no discoverable transcripts (generic, remote).
        """
        ...

    def discover_all(self) -> tuple[SessionRef, ...]:
        """Every session this adapter's store holds, across every cwd — the
        host-wide catalog's discovery unit (epic: Session Catalog).

        NOT the same scan as ``discover_paths(cwd)``: that method answers
        "sessions in *this* cwd" on the 2 s activity poll's hot path and must
        stay cheap (one directory listing for Claude Code; a full store walk
        for Codex, unavoidable given its date-partitioned, cwd-less paths).
        This method answers "every session, host-wide" for a catalog request
        — never called from the poll. Where a per-file head read already
        yields both a row here and a ``discover_paths`` row, the SAME read
        is reused (``_head_cwd_and_birth`` / ``_meta_and_birth``); no adapter
        re-derives a second head-read loop.

        Best-effort per file, newest-first by mtime: a malformed or vanished
        file is skipped, never raised. A file whose head read can't recover a
        cwd still yields a ``SessionRef`` with ``cwd=None`` rather than being
        dropped (~2 % of Claude transcripts on the reference host). ``()`` for
        adapters with no discoverable store (generic).
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
        """Compact ordered slice for the future external-LLM interpreter.

        Minimal in the MVP; the seam exists so a future interpreter never
        has to reshape the adapter contract. Best-effort: an empty or
        missing session yields an empty digest.
        """
        ...

    def session_controls(self, cwd: Path, session_id: str) -> SessionControls:
        """The session's available input controls — a TIER 1 filesystem scan.

        Enumerates the invokable affordances reachable in ``cwd`` with NO running
        session needed: the provider-specific slash commands, skills, and
        configured MCP servers on disk (Claude Code scans the worktree's
        ``.claude/commands`` + skills dirs + ``.mcp.json`` plus the user-level
        cascade; Codex its ``prompts/`` + ``config.toml`` MCP servers). Fills
        ONLY the filesystem-scanned lists — the ``models`` / ``current_model`` /
        ``permission_mode`` dimensions are config concerns the composing manager
        adds (``registry.resolve_models`` etc.), so this stays a pure per-cwd
        read. Remote/shell adapters return
        :meth:`SessionControls.empty` — no local control surface. Best-effort
        like every read here: returns an empty surface on any error, never raises.
        """
        ...

    def final_result(self, cwd: Path, session_id: str) -> FinalResult | None:
        """The session's terminal outcome — the last assistant turn plus
        whether it is truly final.

        A PROJECTION, never a second parser: the filesystem adapters build it
        from their own ``read_messages`` spine via
        ``model.final_result_from_messages``; a remote adapter without a
        message spine (mewbo) derives it from whatever it already parses.
        Best-effort like every read here: ``None`` when no assistant has
        replied yet or the session/transcript can't be read — never raises.
        """
        ...

    def latest_todo(self, cwd: Path, session_id: str) -> TodoList | None:
        """The session's CURRENT todo/checklist state.

        The ``latest_todo`` sibling of :meth:`final_result` — same shape, same
        reason: a PROJECTION over the already-parsed spine, never a second
        parser. Filesystem adapters build it from ``model.
        latest_todo_from_messages(self.read_messages(...))``, reusing the
        identical ``TaskBoard`` fold their turn renderer already performs (a
        "last N turns" tail read is wrong for Claude's split-call Task
        system: a late ``TaskUpdate`` can reference an id whose ``TaskCreate``
        sits arbitrarily far back, so the fold must run from session start).
        Best-effort like every read here: ``None`` when no todo/Task tool has
        been called yet, or the session/transcript can't be read.
        """
        ...

    def latest_task(self, cwd: Path, session_id: str) -> str | None:
        """The session's current task text, UNCAPPED — the same text
        ``parse_activity`` puts on ``AgentActivity.current_task``, minus the
        ``_TASK_TEXT_CAP`` truncation.

        Two consumers, two costs, one selection. ``current_task`` rides the
        ~1 Hz activity delta for every workspace on the host plus every TUI row
        and webapp card, so it is capped at parse time and must stay capped —
        an arbitrarily large pasted prompt on the poll path is exactly what the
        cap exists to prevent. A per-REQUEST reader that renders the text once
        (the issueops sticky comment, inside a collapsed ``<details>``) loses
        nothing but the text, so it reads here instead. Each adapter derives
        both from ONE selection helper, so the capped and uncapped answers can
        never disagree about WHICH text they are returning.

        Best-effort like every read here: ``None`` when the session carries no
        task text at all (a real answer, not an error) or the transcript can't
        be read.
        """
        ...
