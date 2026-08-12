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

import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import ClassVar, Protocol

from loguru import logger

from grove.core.agents.model import (
    AgentActivity,
    AgentMessage,
    FinalResult,
    OrderedDigest,
    QueuedMessage,
    SessionControls,
    SessionRef,
    SessionSummary,
    SessionTurn,
    TodoList,
)


class AgentVersionProbe:
    """One bounded, memoized ``<binary> <flag>`` read — the shared mechanism
    behind :meth:`AgentAdapter.tool_version`.

    It sits beside the Protocol rather than inside one adapter because every
    implementer that *has* a version flag needs the identical discipline, and
    the discipline is the whole difficulty: the answer is asked for on every
    launch, it cannot change under a running process (a new build means a
    reinstall, which means a new process), and a wedged or absent binary must
    cost the launch nothing. The memo is therefore process-lifetime and keyed by
    the resolved argv, so N launches of one agent pay one subprocess and a tool
    that is not installed is probed once and then answers ``None`` for free.

    Concurrency is left to CPython's dict: two launches racing the same cold key
    at worst run the probe twice and store the same answer, which is cheaper
    than serializing every agent's probe behind one lock for the whole timeout.
    """

    TIMEOUT_SECONDS = 5.0
    """Bounds a binary that hangs instead of answering. A version flag is
    instant and offline, so this only ever fires on something already broken."""

    MAX_LENGTH = 200
    """Cap on the recorded string. It becomes a resource attribute on every
    span the agent exports, so a tool that answers with a banner (or with
    something that is not a version at all) must not ride along unbounded."""

    _CACHE: ClassVar[dict[tuple[str, ...], str | None]] = {}

    @staticmethod
    def binary_of(command: str) -> str:
        """The executable (first shell token) of a launch command, or ``""``.

        ``AgentSpec.command`` may carry flags (``codex --full-auto``); a probe
        needs only the binary, and taking it from config is what honors a
        renamed or wrapped tool instead of hard-coding a name here.
        """
        try:
            parts = shlex.split(command)
        except ValueError:  # unbalanced quotes in a hand-edited command
            return ""
        return parts[0] if parts else ""

    @classmethod
    def version(cls, command: str, *flags: str) -> str | None:
        """Whatever ``<binary> <flags>`` printed, VERBATIM, or ``None``.

        Verbatim is the provider boundary: vendors spell their answer
        differently (``codex-cli 0.147.0`` against ``2.1.226 (Claude Code)``),
        and pulling a semver out of that is interpreting provider *semantics*
        rather than normalizing shape. The only shaping is defensive — the first
        non-empty line, stripped and length-capped — because the value ends up
        in an env var an OTel SDK parses.

        Best-effort by contract, like every adapter read: a missing binary, a
        non-zero exit, a timeout or empty output is ``None``. A telemetry nicety
        must never be able to fail a launch.
        """
        binary = cls.binary_of(command)
        if not binary:
            return None
        argv = (binary, *flags)
        if argv in cls._CACHE:
            return cls._CACHE[argv]
        version = cls.probe(argv)
        cls._CACHE[argv] = version
        return version

    @classmethod
    def clear_cache(cls) -> None:
        """Drop the memo — the public seam tests reset between cases, so no test
        has to reach for the private dict (the ``clear_caches()`` convention the
        transcript caches already follow)."""
        cls._CACHE.clear()

    @classmethod
    def probe(cls, argv: tuple[str, ...]) -> str | None:
        """The unmemoized read: run ``argv`` and return its first meaningful
        line, or ``None``.

        Public because it is the one I/O boundary here and therefore the seam
        the suite patches to stay offline — a test patching a *private* symbol
        would make this an implicit contract that a rename silently no-ops.
        """
        try:
            proc = subprocess.run(
                list(argv),  # fixed argv, shell=False, bounded
                capture_output=True,
                text=True,
                timeout=cls.TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            logger.debug("version probe {} failed: {}", argv, exc)
            return None
        if proc.returncode != 0:
            logger.debug("version probe {} exited {}", argv, proc.returncode)
            return None
        for line in proc.stdout.splitlines():
            if line.strip():
                return line.strip()[: cls.MAX_LENGTH]
        return None


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

    ``reports_queue`` declares whether :meth:`pending_queue` can OBSERVE this
    tool's queue at all, which is the one thing an empty tuple cannot say. It is
    a declaration rather than a fourth return value because the answer is a
    property of the tool, fixed for the life of the process, and because the
    alternative is the daemon holding a set of kinds — policy in the layer
    furthest from the evidence. Claude Code / Codex are ``True``; a bare shell
    and a remote orchestrator expose no queue, so ``False``, and the wire says
    ``supported=False`` rather than claiming an empty one.

    ``reports_tool_errors`` declares whether this tool records a STRUCTURAL
    failure flag on a tool result — the ``reports_queue`` shape applied to
    ``ContentBlock.is_error``, and for the same reason: only the adapter knows,
    and the alternative is a provider-name list baked into whatever aggregates
    the flag, one layer removed from the evidence. It answers the question a
    count of errors cannot: does ``is_error=False`` mean *the call succeeded*,
    or merely *this format has no way to say*. Claude Code fills the field
    natively; Codex's tool-output record has no such key in any version, so a
    failed Codex call is honestly ``ok`` with its error text in the prose (and
    reading THAT would be interpreting the tool's semantics — the provider
    boundary). Adapters with no message spine at all answer ``False`` again,
    for a third reason: there is no tool result to flag.

    **Consumers must use it as a DENOMINATOR, not as a filter.** A population
    mixing a reporting tool with a silent one has a real error count and a
    smaller measurable population than its call count, so dividing by the calls
    deflates every rate by that scope's share of silent calls — and reports the
    deflated number with full confidence.
    """

    kind: str
    remote: bool
    resumable: bool
    reports_queue: bool
    reports_tool_errors: bool

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

    def tool_version(self, command: str) -> str | None:
        """The version string this tool reports for itself, VERBATIM, or ``None``.

        Identity for an exported trace: a reader looking at a span needs to know
        which *build* of the agent produced it, and only the tool can say. Like
        :meth:`available_models` it takes the agent's configured ``command`` so
        the binary comes from config rather than a hard-coded name, and like it
        the answer is best-effort — every implementation runs through
        :class:`AgentVersionProbe`, which bounds and memoizes the subprocess.

        ``None`` is a real answer, not a degradation: a tool whose work happens
        on a backend (mewbo) has no local build to report, and a bare shell has
        no version at all. The provider boundary rules out guessing one, and an
        absent value is an OMITTED attribute rather than an invented ``unknown``.
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

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        """The session's agentic-loop spine — the lineage-preserving message list
        every content projection derives from, oldest first.

        ``read_turns`` and ``transcript_digest`` are projections of this, and a
        trace replay walks it to emit one observation per assistant message and
        tool call. Provider-neutral by construction (role, content blocks, ids,
        and token usage where the provider reports it), which is what lets a
        consumer read any tool's content with no per-tool branch.

        **An adapter with no message spine returns ``()``, and that is an ANSWER
        rather than a degradation:** a remote session's history lives behind an
        API and a bare shell records nothing, so "there is no content here to
        replay" is the honest thing for a content consumer to act on. Best-effort
        like every read here — ``()`` when the session or its backing store
        cannot be read, never a raise.
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

    def pending_queue(self, cwd: Path, session_id: str) -> tuple[QueuedMessage, ...]:
        """What the HARNESS is holding for this session but has not delivered yet,
        in the order the harness reports.

        The :meth:`latest_todo` sibling in shape and in contract: a read of
        something the tool already owns, never a Grove-side ledger. Both shipped
        harnesses queue a message typed while the agent is busy and decide
        themselves when to inject it, so a second queue here would be a second
        writer with no arbitration — it would drift the instant somebody typed
        straight into the pane.

        Where the queue LIVES is the per-provider difference this seam exists to
        absorb: Claude Code writes ``queue-operation`` records into the
        transcript Grove already folds, Codex keeps a SQLite table under its
        config root, and neither a bare shell nor a remote orchestrator exposes
        one at all.

        ``()`` is deliberately ambiguous HERE and disambiguated one layer up: an
        adapter that cannot see a queue and an adapter whose queue is empty both
        answer ``()``, and the wire shape carries the ``supported`` flag that
        tells them apart (``WorkspaceQueueView``). Best-effort like every read
        here: an unreadable store is ``()``, never a raise.
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
