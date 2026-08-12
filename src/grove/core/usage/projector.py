"""Project normalized adapter messages into the rebuildable usage cache."""

# The SQL insert tuples below intentionally mirror the frozen cache schema;
# keep the projection readable while the schema remains the source of truth.
# ruff: noqa: E501

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from loguru import logger

from grove.core.agents import AgentMessage, all_adapters, get_adapter
from grove.core.agents.model import AgentActivity, SessionRef
from grove.core.config import AgentSpec, GroveConfig
from grove.core.contracts.usage import UsageProvider
from grove.core.git import detect_root
from grove.core.manager import WorkspaceManager
from grove.core.registry import RepoRegistry
from grove.core.usage._command import SHELL_TOOL_NAMES, LeadingCommand
from grove.core.usage._intervals import ActiveIntervals, WorkIntervals
from grove.core.usage._pricing import PriceBook, TokenCounts
from grove.core.usage._store import FileFingerprint, UsageStore, complete_bytes
from grove.core.usage.quota import QuotaAccount
from grove.core.workspace import TranscriptContext


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """Counts from one best-effort projection pass."""

    indexed_sources: int = 0
    changed_sources: int = 0
    degraded_sources: int = 0


class _UsageAdapter(Protocol):
    kind: str

    def discover_all(self) -> tuple[SessionRef, ...]: ...

    def locate_transcripts(self, cwd: Path, session_id: str) -> list[Path]: ...

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]: ...

    def parse_activity(self, cwd: Path, session_id: str) -> AgentActivity: ...


@dataclass(frozen=True, slots=True)
class _ToolCall:
    """What a `tool_use` block must carry forward onto its `tool_result` row.

    Grew from a bare tuple when `background` joined it: the duration lives on
    the result and the attribution on the call, so every fact the ranking needs
    has to cross that gap together.
    """

    tool_name: str | None
    target: str | None
    started: datetime | None
    background: bool


@dataclass(frozen=True, slots=True)
class _Reference:
    adapter: _UsageAdapter
    ref: SessionRef
    profile_root: Path


class UsageProjector:
    """Read adapter-normalized messages and replace derived rows atomically."""

    def __init__(
        self,
        *,
        cfg: GroveConfig,
        registry: RepoRegistry,
        store: UsageStore,
        clock: Callable[[], datetime] | None = None,
        adapters: Sequence[_UsageAdapter] | None = None,
    ) -> None:
        self._cfg = cfg
        self._registry = registry
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._prices = PriceBook(cfg.usage.pricing)
        self._adapters = tuple(adapters) if adapters is not None else None
        # One parser for the whole refresh: it holds a compiled pattern and the
        # normalization map, and building one per session would recompile both
        # thousands of times.
        self._leading = LeadingCommand(cfg.usage.commands)

    def refresh(self, *, force: bool = False) -> ProjectionResult:
        """Index changed reachable sessions; ``force`` only bypasses mtime skip."""
        if not self._cfg.usage.enabled:
            return ProjectionResult()
        now = int(self._clock().timestamp())
        retention_policy = str(self._cfg.usage.retention_days)
        previous_retention = self._store.meta("retention_days")
        if previous_retention is not None and previous_retention != retention_policy:
            force = True
        refs, discovery_degraded = self._references(now)
        changed_sources = self._prune_vanished()
        degraded_sources: set[str] = set(discovery_degraded)
        # Built lazily, once, on the first ref that actually needs replacing —
        # not once per session. `manager.list()` reconciles live git/tmux
        # state per repo; profiled at 394 calls / 3.1s cumulative for 79
        # sessions when resolved per-session inside `_workspace_id`. Every
        # session in one refresh answers the same "which workspace owns this
        # session id" question against the same fleet snapshot, so one index
        # built up front serves every lookup this refresh needs.
        workspace_index: dict[tuple[str, str], str] | None = None
        for item in refs:
            adapter, ref, profile_root = item.adapter, item.ref, item.profile_root
            path = ref.transcript_path
            if path is None:
                continue
            source_id = self._source_id(ref.adapter_kind, profile_root)
            if ref.cwd is None:
                degraded_sources.add(source_id)
                self._mark_degraded(ref, profile_root, now, "transcript cwd was not measured")
                continue
            try:
                with WorkspaceManager.transcript_config_dir_scope(adapter.kind, str(profile_root)):
                    paths = _transcript_paths(adapter, Path(ref.cwd), ref.session_id, path)
                    before = _fingerprints(paths)
                    if not before:
                        continue
                    previous = self._store.query(
                        "SELECT path, inode, size, mtime_ns FROM ingested_files "
                        "WHERE session_id = ? AND source_id = ?",
                        (ref.session_id, source_id),
                    )
                    if not force and _same_fingerprints(previous, before):
                        continue
                    changed_sources.add(source_id)
                    messages = adapter.read_messages(Path(ref.cwd), ref.session_id)
                    activity = adapter.parse_activity(Path(ref.cwd), ref.session_id)
                    after_paths = _transcript_paths(adapter, Path(ref.cwd), ref.session_id, path)
                after = _fingerprints(after_paths)
                if not after or not set(before).issubset(after):
                    raise OSError("transcript vanished during projection")
                if workspace_index is None:
                    workspace_index = self._workspace_index()
                self._replace(
                    adapter_kind=ref.adapter_kind,
                    ref=ref,
                    messages=messages,
                    activity=activity,
                    profile_root=profile_root,
                    # Persist the snapshot from BEFORE the adapter read. If a
                    # transcript grows during parsing, the next refresh sees
                    # the mismatch and reads the appended bytes instead of
                    # blessing an unread post-read fingerprint as complete.
                    fingerprints=before,
                    now=now,
                    workspace_id=workspace_index.get((ref.session_id, ref.adapter_kind)),
                )
            except Exception as exc:  # one transcript must not poison the index
                degraded_sources.add(source_id)
                logger.warning("usage projection degraded for {}: {}", path, type(exc).__name__)
                self._mark_degraded(ref, profile_root, now, str(exc))
        if degraded_sources:
            with self._store.write() as conn:
                conn.executemany(
                    "UPDATE sources SET health='degraded' WHERE source_id=?",
                    [(source_id,) for source_id in degraded_sources],
                )
        self._apply_retention(now)
        self._store.set_meta("retention_days", retention_policy)
        self._store.set_meta("last_refresh_at", str(now))
        return ProjectionResult(
            indexed_sources=len(
                {self._source_id(item.ref.adapter_kind, item.profile_root) for item in refs}
                | discovery_degraded
            ),
            changed_sources=len(changed_sources),
            degraded_sources=len(degraded_sources),
        )

    def _prune_vanished(self) -> set[str]:
        """Drop derived sessions whose adapter-owned files no longer exist."""
        rows = self._store.query(
            "SELECT path, source_id, session_id FROM ingested_files WHERE session_id IS NOT NULL"
        )
        vanished = {
            (row["source_id"], row["session_id"]) for row in rows if not Path(row["path"]).exists()
        }
        if not vanished:
            return set()
        with self._store.write() as conn:
            for source_id, session_id in vanished:
                conn.execute(
                    "DELETE FROM usage_events WHERE source_id=? AND session_id=?",
                    (source_id, session_id),
                )
                conn.execute(
                    "DELETE FROM sessions WHERE source_id=? AND session_id=?",
                    (source_id, session_id),
                )
                conn.execute(
                    "DELETE FROM ingested_files WHERE source_id=? AND session_id=?",
                    (source_id, session_id),
                )
        return {source_id for source_id, _ in vanished}

    def _references(self, now: int) -> tuple[list[_Reference], set[str]]:
        refs: list[_Reference] = []
        degraded: set[str] = set()
        seen: set[tuple[str, str, str]] = set()
        adapters = self._adapters or tuple(
            cast(_UsageAdapter, adapter)
            for adapter in all_adapters()
            if adapter.kind in TranscriptContext.CONFIG_DIR_ENV
        )
        by_kind = {adapter.kind: adapter for adapter in adapters}
        scans: list[tuple[_UsageAdapter, Path | None]] = [(adapter, None) for adapter in adapters]
        for kind, root in self._declared_profile_roots():
            adapter = by_kind.get(kind)
            if adapter is None:
                adapter = cast(_UsageAdapter, get_adapter(kind))
                by_kind[kind] = adapter
            scans.append((adapter, root))
        scanned: set[tuple[str, str | None]] = set()
        for adapter, configured_root in scans:
            scan_key = (adapter.kind, str(configured_root) if configured_root else None)
            if scan_key in scanned:
                continue
            scanned.add(scan_key)
            scope = (
                WorkspaceManager.transcript_config_dir_scope(adapter.kind, str(configured_root))
                if configured_root is not None
                else contextlib.nullcontext()
            )
            try:
                with scope:
                    discovered = adapter.discover_all()
            except Exception as exc:
                logger.warning(
                    "usage discovery degraded for {}: {}", adapter.kind, type(exc).__name__
                )
                source_id = self._mark_discovery_degraded(
                    adapter.kind, configured_root, now, str(exc)
                )
                degraded.add(source_id)
                continue
            for ref in discovered:
                if ref.transcript_path is None:
                    continue
                root = configured_root or _profile_root(adapter.kind, ref.transcript_path)
                key = (adapter.kind, str(root), ref.session_id)
                if key not in seen:
                    seen.add(key)
                    refs.append(_Reference(adapter=adapter, ref=ref, profile_root=root))
        return refs, degraded

    def _mark_discovery_degraded(
        self,
        adapter_kind: str,
        profile_root: Path | None,
        now: int,
        detail: str,
    ) -> str:
        root = profile_root or Path(f"<{adapter_kind}-default>")
        source_id = self._source_id(adapter_kind, root)
        label = root.name if profile_root is not None else f"{adapter_kind} default"
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO sources(source_id, provider, root, label, health, detail, last_indexed_at) "
                "VALUES(?,?,?,?, 'degraded', ?, ?) ON CONFLICT(source_id) DO UPDATE SET "
                "health='degraded', detail=excluded.detail, last_indexed_at=excluded.last_indexed_at",
                (source_id, _provider(adapter_kind), str(root), label, detail[:300], now),
            )
        return source_id

    def _declared_profile_roots(self) -> tuple[tuple[str, Path], ...]:
        specs: list[AgentSpec] = list(self._cfg.agents)
        roots: set[tuple[str, Path]] = set()
        for root in self._registry.known_roots():
            try:
                manager = self._registry.get(root)
                specs.extend(manager.config.agents)
                for state in manager.list():
                    ctx = state.transcript_context
                    kind = manager.effective_kind(state)
                    if (
                        ctx is not None
                        and ctx.config_dir
                        and kind in TranscriptContext.CONFIG_DIR_ENV
                    ):
                        roots.add((kind, Path(ctx.config_dir).expanduser().resolve()))
            except Exception as exc:
                logger.debug("usage profile discovery skipped {}: {}", root, type(exc).__name__)
        for spec in specs:
            var = TranscriptContext.CONFIG_DIR_ENV.get(spec.kind)
            raw = spec.env.get(var, "") if var else ""
            for value in raw.split(",") if spec.kind == "claude_code" else (raw,):
                if value.strip():
                    roots.add((spec.kind, Path(value.strip()).expanduser().resolve()))
        return tuple(sorted(roots, key=lambda item: (item[0], str(item[1]))))

    @staticmethod
    def _source_id(provider: str, profile_root: Path) -> str:
        root = str(profile_root)
        digest = hashlib.sha256(f"{provider}\0{root}".encode()).hexdigest()[:12]
        return f"{provider}-{digest}"

    def _replace(
        self,
        *,
        adapter_kind: str,
        ref: Any,
        messages: Iterable[AgentMessage],
        activity: AgentActivity,
        profile_root: Path,
        fingerprints: dict[Path, FileFingerprint],
        now: int,
        workspace_id: str | None,
    ) -> None:
        source_id = self._source_id(adapter_kind, profile_root)
        account = QuotaAccount.mint(
            provider=_provider(adapter_kind),
            root=profile_root,
            labels=self._cfg.usage.quota.labels,
        )
        session = _session_row(
            adapter_kind=adapter_kind,
            ref=ref,
            messages=tuple(messages),
            activity=activity,
            source_id=source_id,
            account=account,
            prices=self._prices,
            project=_project(ref.cwd),
            workspace_id=workspace_id,
        )
        events = _event_rows(
            adapter_kind,
            ref.session_id,
            source_id,
            session["messages"],
            provider_total=session["provider_total"],
            leading=self._leading,
        )
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO sources(source_id, provider, root, label, account_id, health, detail, last_indexed_at) "
                "VALUES(?, ?, ?, ?, ?, 'ok', NULL, ?) ON CONFLICT(source_id) DO UPDATE SET "
                "health='ok', detail=NULL, last_indexed_at=excluded.last_indexed_at",
                (
                    source_id,
                    _provider(adapter_kind),
                    str(profile_root),
                    account.label,
                    account.account_id,
                    now,
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO accounts(account_id, provider, label, billing_mode) VALUES(?, ?, ?, 'unknown')",
                (account.account_id, account.provider, account.label),
            )
            conn.execute(
                "DELETE FROM usage_events WHERE session_id=? AND source_id=?",
                (ref.session_id, source_id),
            )
            conn.execute(
                "DELETE FROM sessions WHERE session_id=? AND source_id=?",
                (ref.session_id, source_id),
            )
            conn.execute(
                "DELETE FROM ingested_files WHERE session_id=? AND source_id=?",
                (ref.session_id, source_id),
            )
            conn.execute(
                "INSERT INTO sessions(session_id, source_id, provider, cwd, project, account_id, workspace_id, started_at, last_event_at, "
                "turns, tool_calls, tool_failures, files_changed, active_ms, execution_ms, generation_ms, tool_ms, elapsed_span_ms, duration_confidence, "
                "fresh_input, cache_read, cache_creation, reasoning, output, provider_total, "
                "subagent_fresh_input, subagent_cache_read, subagent_cache_creation, subagent_reasoning, subagent_output, subagent_provider_total, "
                "cost_amount, cost_currency, "
                "cost_provenance, models, parser_health, parser_detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(session[key] for key in _SESSION_COLUMNS),
            )
            conn.executemany(
                "INSERT INTO usage_events(session_id, source_id, seq, ts, kind, model, tool_name, target, failure_category, is_error, duration_ms, duration_source, thread_id, fresh_input, cache_read, cache_creation, reasoning, output, provider_total, attrs_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                events,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO ingested_files(path, source_id, session_id, inode, size, mtime_ns, cursor, ingested_at) VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        str(source_path),
                        source_id,
                        ref.session_id,
                        fingerprint.inode,
                        fingerprint.size,
                        fingerprint.mtime_ns,
                        complete_bytes(source_path),
                        now,
                    )
                    for source_path, fingerprint in fingerprints.items()
                ],
            )

    def _mark_degraded(
        self,
        ref: SessionRef,
        profile_root: Path,
        now: int,
        detail: str,
    ) -> None:
        source_id = self._source_id(ref.adapter_kind, profile_root)
        account = QuotaAccount.mint(
            provider=_provider(ref.adapter_kind),
            root=profile_root,
            labels=self._cfg.usage.quota.labels,
        )
        with self._store.write() as conn:
            conn.execute(
                "INSERT INTO sources(source_id, provider, root, label, health, detail, last_indexed_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET health='degraded', detail=excluded.detail, last_indexed_at=excluded.last_indexed_at",
                (
                    source_id,
                    _provider(ref.adapter_kind),
                    str(profile_root),
                    account.label,
                    "degraded",
                    detail[:300],
                    now,
                ),
            )

    def _apply_retention(self, now: int) -> None:
        days = self._cfg.usage.retention_days
        if days is None:
            return
        cutoff = now - days * 86_400
        with self._store.write() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE (last_event_at IS NOT NULL AND last_event_at < ?) "
                "OR EXISTS (SELECT 1 FROM usage_events e WHERE "
                "e.session_id=sessions.session_id AND e.source_id=sessions.source_id "
                "AND e.ts IS NOT NULL AND e.ts < ?)",
                (cutoff, cutoff),
            )
            conn.execute(
                "DELETE FROM usage_events WHERE NOT EXISTS (SELECT 1 FROM sessions s WHERE "
                "s.session_id=usage_events.session_id AND s.source_id=usage_events.source_id)"
            )

    def _workspace_index(self) -> dict[tuple[str, str], str]:
        """``(agent_session_id, adapter_kind) -> owning workspace id``, whole fleet.

        Every ref in one refresh answers the same "which workspace owns this
        session id" question against the same fleet snapshot, so this walks
        `RepoRegistry.known_roots()` and each repo's `manager.list()` — the
        expensive live git/tmux reconciliation — exactly once per refresh
        rather than once per session. ``session_duration.py`` imports
        `_derived_intervals` from this module and must keep working if this
        moves; it does not touch this seam.
        """
        index: dict[tuple[str, str], str] = {}
        for root in self._registry.known_roots():
            try:
                manager = self._registry.get(root)
                for state in manager.list():
                    if state.agent_session_id is None:
                        continue
                    index[(state.agent_session_id, manager.effective_kind(state))] = state.id
            except Exception:
                continue
        return index


def _provider(kind: str) -> UsageProvider:
    return "claude_code" if kind == "claude_code" else "codex" if kind == "codex" else "generic"


def _transcript_paths(
    adapter: _UsageAdapter, cwd: Path, session_id: str, primary: Path
) -> tuple[Path, ...]:
    """All adapter-owned files, with the discovered primary retained first."""
    located = adapter.locate_transcripts(cwd, session_id)
    return tuple(dict.fromkeys((primary, *located)))


def _fingerprints(paths: Iterable[Path]) -> dict[Path, FileFingerprint]:
    return {
        path: fingerprint for path in paths if (fingerprint := FileFingerprint.of(path)) is not None
    }


def _same_fingerprints(rows: Iterable[Any], current: dict[Path, FileFingerprint]) -> bool:
    previous = {
        Path(row["path"]): FileFingerprint(
            inode=row["inode"], size=row["size"], mtime_ns=row["mtime_ns"]
        )
        for row in rows
    }
    return previous == current


def _profile_root(kind: str, transcript: Path) -> Path:
    marker = "projects" if kind == "claude_code" else "sessions" if kind == "codex" else None
    if marker is not None:
        for parent in transcript.parents:
            if parent.name == marker:
                return parent.parent.resolve()
    return transcript.parent.resolve()


def _project(cwd: str | None) -> str | None:
    if cwd is None:
        return None
    try:
        root = detect_root(Path(cwd))
    except Exception:
        return None
    return str(root) if root is not None else None


def _epoch(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value is not None else None


def _epoch_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def _sum(values: Iterable[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present, 0) if present else None


def _session_row(
    *,
    adapter_kind: str,
    ref: Any,
    messages: tuple[AgentMessage, ...],
    activity: AgentActivity,
    source_id: str,
    account: QuotaAccount,
    prices: PriceBook,
    project: str | None,
    workspace_id: str | None,
) -> dict[str, Any]:
    timestamps = [m.timestamp for m in messages if m.timestamp is not None]
    usages = [m.usage for m in messages if m.usage is not None]
    fresh = _sum(u.input for u in usages)
    cache_read = _sum(u.cache_read for u in usages)
    cache_creation = _sum(u.cache_creation for u in usages)
    reasoning = _sum(u.reasoning for u in usages)
    output = _sum(u.output for u in usages)
    # `messages` already carries sub-agent (sidechain) usage alongside the
    # root thread's — `is_sidechain` is on the spine, so this is a partition
    # of a sum computed above, never a second read. The totals above stay the
    # COMBINED root+delegated figure (attribution: a session's total includes
    # the work it delegated); these are the delegated PORTION alone, kept
    # separately so a reader can see "of the total, this much was sub-agents"
    # without Grove folding the two into one opaque number.
    subagent_usages = [m.usage for m in messages if m.usage is not None and m.is_sidechain]
    subagent_fresh_input = _sum(u.input for u in subagent_usages)
    subagent_cache_read = _sum(u.cache_read for u in subagent_usages)
    subagent_cache_creation = _sum(u.cache_creation for u in subagent_usages)
    subagent_reasoning = _sum(u.reasoning for u in subagent_usages)
    subagent_output = _sum(u.output for u in subagent_usages)
    provider_total = None
    if adapter_kind == "codex" and (activity.tokens_in or activity.tokens_out):
        fresh = None
        output = activity.tokens_out
        provider_total = activity.tokens_in + activity.tokens_out
    models = tuple(
        dict.fromkeys(
            [*(m.model for m in messages if m.model), *([activity.model] if activity.model else [])]
        )
    )
    counts = TokenCounts(
        fresh_input=fresh, cache_read=cache_read, cache_creation=cache_creation, output=output
    )
    amount = prices.amount(models[0] if len(models) == 1 else None, counts)
    files = {
        str(target)
        for m in messages
        for b in m.content
        if b.type == "tool_use" and b.tool_name in {"Edit", "Write", "MultiEdit", "apply_patch"}
        for target in _tool_targets(b.tool_input)
    }
    start = min(timestamps) if timestamps else ref.birth
    end = max(timestamps) if timestamps else datetime.fromtimestamp(ref.mtime, UTC)
    intervals = _derived_intervals(messages)
    combined = intervals.combined
    active_ms = combined.union_ms()
    return {
        "session_id": ref.session_id,
        "source_id": source_id,
        "provider": _provider(adapter_kind),
        "cwd": ref.cwd,
        "project": project,
        "account_id": account.account_id,
        "workspace_id": workspace_id,
        "started_at": _epoch(start),
        "last_event_at": _epoch(end),
        "turns": sum(int(m.role == "user") for m in messages),
        "tool_calls": sum(len(m.tool_names()) for m in messages),
        "tool_failures": sum(
            1 for m in messages for b in m.content if b.type == "tool_result" and b.is_error
        ),
        "files_changed": len(files),
        "active_ms": active_ms,
        "execution_ms": combined.sum_ms(),
        # The two halves `execution_ms` folds together. Written from the same
        # single pass, so `generation_ms + tool_ms == execution_ms` whenever
        # all three are measured — a property of the sum reducer, not an
        # arithmetic coincidence to re-check here. No union counterpart: two
        # unions cannot be added (see `WorkIntervals`).
        "generation_ms": intervals.generation.sum_ms(),
        "tool_ms": intervals.tool.sum_ms(),
        "elapsed_span_ms": (int((end - start).total_seconds() * 1000) if start and end else None),
        "duration_confidence": "derived" if active_ms is not None or (start and end) else "unknown",
        "fresh_input": fresh,
        "cache_read": cache_read,
        "cache_creation": cache_creation,
        "reasoning": reasoning,
        "output": output,
        "provider_total": provider_total,
        "subagent_fresh_input": subagent_fresh_input,
        "subagent_cache_read": subagent_cache_read,
        "subagent_cache_creation": subagent_cache_creation,
        "subagent_reasoning": subagent_reasoning,
        "subagent_output": subagent_output,
        # No adapter reports a per-sub-agent provider total today (Codex never
        # produces `is_sidechain` messages through this path); nullable rather
        # than omitted so `_tokens(row, prefix="subagent_")` — the same generic
        # reader every other token-class column goes through — needs no
        # special case, and a future provider that does report one costs no
        # schema bump.
        "subagent_provider_total": None,
        "cost_amount": format(amount, "f") if amount is not None else None,
        "cost_currency": prices.currency,
        "cost_provenance": "estimated" if amount is not None else "unknown",
        "models": json.dumps(models),
        "parser_health": "ok",
        "parser_detail": None,
        "messages": messages,
    }


def _event_rows(
    kind: str,
    session_id: str,
    source_id: str,
    messages: tuple[AgentMessage, ...],
    *,
    provider_total: int | None,
    leading: LeadingCommand,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    seq = 0
    calls: dict[str, _ToolCall] = {}
    previous_by_thread: dict[str | None, tuple[str, datetime]] = {}
    last_assistant = max(
        (index for index, message in enumerate(messages) if message.role == "assistant"),
        default=-1,
    )
    for message_index, message in enumerate(messages):
        if message.role == "assistant":
            seq += 1
            usage = message.usage
            generation_ms = _generation_duration_ms(message, previous_by_thread)
            rows.append(
                (
                    session_id,
                    source_id,
                    seq,
                    _epoch(message.timestamp),
                    "subagent" if message.is_sidechain else "generation",
                    message.model,
                    None,
                    None,
                    None,
                    0,
                    generation_ms,
                    "derived" if generation_ms is not None else None,
                    message.thread_id,
                    usage.input if usage else None,
                    usage.cache_read if usage else None,
                    usage.cache_creation if usage else None,
                    usage.reasoning if usage else None,
                    usage.output if usage else None,
                    provider_total if kind == "codex" and message_index == last_assistant else None,
                    json.dumps({"adapter_kind": kind}),
                )
            )
        for block in message.content:
            if block.type not in {"tool_use", "tool_result"}:
                continue
            seq += 1
            tool_name = block.tool_name
            # A shell call names no file, so `target` was NULL for every one of
            # them. It carries the leading executable instead — the one column
            # that makes "which processes eat the agent's time" answerable.
            shell = tool_name in SHELL_TOOL_NAMES
            target = (
                leading.of(LeadingCommand.command_text(block.tool_input))
                if shell
                else _tool_target(block.tool_input)
            )
            background = shell and LeadingCommand.in_background(block.tool_input)
            duration_ms = None
            duration_source = None
            if block.type == "tool_use":
                if block.tool_use_id:
                    calls[block.tool_use_id] = _ToolCall(
                        tool_name, target, message.timestamp, background
                    )
                event_kind = (
                    "file_edit"
                    if tool_name in {"Edit", "Write", "MultiEdit", "apply_patch"}
                    else "tool_call"
                )
            else:
                event_kind = "tool_result"
                call = calls.get(block.tool_use_id or "")
                if call is not None:
                    # The RESULT row is the one carrying the duration, so the
                    # call's attribution has to travel forward onto it or the
                    # ranking has no time to rank by.
                    tool_name, target, background = call.tool_name, call.target, call.background
                    if call.started is not None and message.timestamp is not None:
                        duration_ms = max(
                            0, int((message.timestamp - call.started).total_seconds() * 1000)
                        )
                        duration_source = "derived"
            rows.append(
                (
                    session_id,
                    source_id,
                    seq,
                    _epoch(message.timestamp),
                    event_kind,
                    message.model,
                    tool_name,
                    str(target) if target else None,
                    "error" if block.is_error else None,
                    int(block.is_error),
                    duration_ms,
                    duration_source,
                    message.thread_id,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    # Written only when true, so `attrs_json` stays NULL for the
                    # overwhelming majority of rows and the column keeps costing
                    # nothing for the 97%+ of calls that are foreground.
                    json.dumps({"background": True}) if background else None,
                )
            )
        if message.timestamp is not None:
            previous_by_thread[message.thread_id] = (message.role, message.timestamp)
    return rows


def _generation_duration_ms(
    message: AgentMessage,
    previous_by_thread: dict[str | None, tuple[str, datetime]],
) -> int | None:
    if message.timestamp is None:
        return None
    previous = previous_by_thread.get(message.thread_id)
    if previous is None or previous[0] not in {"user", "tool"}:
        return None
    return max(0, int((message.timestamp - previous[1]).total_seconds() * 1000))


def _derived_intervals(messages: tuple[AgentMessage, ...]) -> WorkIntervals:
    """Timestamp-supported generation and tool intervals, human waits excluded.

    A user/tool record followed by an assistant generation, and a tool call
    followed by its correlated result, are evidence that an agent was working;
    the assistant-final → next-user gap is a human thinking and is left out.

    Every sub-agent thread's intervals land in the same halves, because the
    reduction is the caller's choice: the union is the session's wall clock and
    the sum is its labour total, and on a fleet session the two differ by
    however much concurrency the task bought.

    The two kinds are collected in ONE pass and returned apart rather than
    pre-folded, so ``execution_ms`` and its generation/tool partition come from
    the same walk over the same spine. A second derivation would be a second
    answer to "which spans of a transcript are work", which is precisely what
    ``ActiveIntervals`` was created to stop.
    """
    previous_by_thread: dict[str | None, tuple[str, datetime]] = {}
    calls: dict[str, datetime] = {}
    generation: list[tuple[int, int]] = []
    tool: list[tuple[int, int]] = []
    for message in messages:
        if message.role == "assistant" and message.timestamp is not None:
            previous = previous_by_thread.get(message.thread_id)
            if previous is not None and previous[0] in {"user", "tool"}:
                generation.append((_epoch_ms(previous[1]), _epoch_ms(message.timestamp)))
        for block in message.content:
            if block.type == "tool_use" and block.tool_use_id and message.timestamp is not None:
                calls[block.tool_use_id] = message.timestamp
            elif (
                block.type == "tool_result"
                and block.tool_use_id
                and message.timestamp is not None
                and block.tool_use_id in calls
            ):
                tool.append((_epoch_ms(calls[block.tool_use_id]), _epoch_ms(message.timestamp)))
        if message.timestamp is not None:
            previous_by_thread[message.thread_id] = (message.role, message.timestamp)
    return WorkIntervals(
        generation=ActiveIntervals.of(generation),
        tool=ActiveIntervals.of(tool),
    )


def _tool_target(raw: dict[str, Any] | None) -> str | None:
    targets = _tool_targets(raw)
    return targets[0] if targets else None


def _tool_targets(raw: dict[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(raw, dict):
        return ()
    for key in ("file_path", "path"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return (value,)
    patch = raw.get("input")
    if not isinstance(patch, str):
        return ()
    return tuple(
        dict.fromkeys(
            match.group(1).strip()
            for match in re.finditer(
                r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", patch, re.MULTILINE
            )
            if match.group(1).strip()
        )
    )


_SESSION_COLUMNS = (
    "session_id",
    "source_id",
    "provider",
    "cwd",
    "project",
    "account_id",
    "workspace_id",
    "started_at",
    "last_event_at",
    "turns",
    "tool_calls",
    "tool_failures",
    "files_changed",
    "active_ms",
    "execution_ms",
    "generation_ms",
    "tool_ms",
    "elapsed_span_ms",
    "duration_confidence",
    "fresh_input",
    "cache_read",
    "cache_creation",
    "reasoning",
    "output",
    "provider_total",
    "subagent_fresh_input",
    "subagent_cache_read",
    "subagent_cache_creation",
    "subagent_reasoning",
    "subagent_output",
    "subagent_provider_total",
    "cost_amount",
    "cost_currency",
    "cost_provenance",
    "models",
    "parser_health",
    "parser_detail",
)
