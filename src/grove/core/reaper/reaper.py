"""Two-phase reconciliation: mark what no record entitles, sweep it if it is still there."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

from loguru import logger

from grove.core import paths
from grove.core.errors import GroveError
from grove.core.reaper.collectors import Collector
from grove.core.reaper.ledger import Ledger, ResourceClass, ResourceRef
from grove.core.reaper.store import Action, ReaperRow, ReaperStore
from grove.core.store import JsonWorkspaceStore
from grove.core.telemetry.semconv import GroveIdentityAttr
from grove.core.trace import SpanRecord, SpanSink


@dataclass(frozen=True, slots=True)
class Waiting:
    """A candidate marked on an earlier pass and still inside its grace.

    Reported, never logged: the action log records what a pass DID, and a
    candidate that is merely waiting would otherwise append a row every pass.
    """

    ref: ResourceRef
    first_seen: datetime
    sweepable_at: datetime


@dataclass(frozen=True, slots=True)
class PassReport:
    """What one pass saw and did.

    Attributes:
        rows: Every action recorded, in the order taken.
        standing: Candidates still present when the pass ended, per class.
        waiting: Candidates marked earlier and still inside their grace.
    """

    rows: tuple[ReaperRow, ...]
    standing: dict[ResourceClass, int] = field(default_factory=dict)
    waiting: tuple[Waiting, ...] = ()

    @property
    def found_anything(self) -> bool:
        """Whether any candidate exists or was removed: what ``grove vacuum`` exits 1 on.

        A candidate still in its grace counts: it is a leak the next pass will
        remove, and a host check that passed over it would read the host clean.
        """
        return bool(self.rows or self.waiting)


class Reaper:
    """Reconciles what exists on this host against what the workspace records entitle.

    A candidate is MARKED the first time a pass sees it and SWEPT only on a
    later pass at least its collector's grace after that first sighting. The
    sighting is persisted, so a daemon restart between the two does not reset
    the grace. Each removal holds the workspace store's lock and re-reads the
    records under it: ``create`` saves its record under that lock BEFORE it
    starts any server or worker, so a resource a create is bringing up is
    always either entitled by the re-read or not yet in existence.
    """

    def __init__(
        self,
        *,
        collectors: Sequence[Collector],
        workspaces: JsonWorkspaceStore,
        store: ReaperStore,
        sink: SpanSink | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Wire one reaper.

        Args:
            collectors: One per resource class to reconcile.
            workspaces: The workspace records that entitle resources.
            store: The durable marks and action log.
            sink: Where each action's span goes; ``None`` when telemetry is off.
            clock: The time source, injected so a test can step past a grace.
        """
        self._collectors = {collector.cls: collector for collector in collectors}
        self._workspaces = workspaces
        self._store = store
        self._sink = sink
        self._clock = clock
        self._running = Lock()

    def run(self, *, apply: bool, classes: frozenset[ResourceClass] | None = None) -> PassReport:
        """Run at most one reconciliation pass at a time.

        Args:
            apply: Remove swept candidates instead of reporting them.
            classes: Restrict the pass to these classes; ``None`` means every collector.

        Returns:
            The completed pass, or an empty report when another pass is still running.
        """
        if not self._running.acquire(blocking=False):
            logger.info("reaper: pass skipped while another pass is running")
            return PassReport(rows=())
        try:
            return self._run(apply=apply, classes=classes)
        finally:
            self._running.release()

    def _run(self, *, apply: bool, classes: frozenset[ResourceClass] | None = None) -> PassReport:
        """One reconciliation pass.

        Args:
            apply: Remove swept candidates. ``False`` records what a sweep would
                remove as ``report`` and changes nothing on the host.
            classes: Restrict the pass to these classes; ``None`` means every
                collector.

        Returns:
            Every action taken, and what is still standing.
        """
        now = self._clock()
        found = [
            ref
            for cls, collector in self._collectors.items()
            if classes is None or cls in classes
            for ref in collector.found()
        ]
        candidates = Ledger.candidates(found, self._workspaces.load_all())
        first_seen = self._store.first_seen(candidates)
        rows: list[ReaperRow] = []
        waiting: list[Waiting] = []
        for ref in sorted(candidates):
            seen = first_seen.get(ref)
            grace = self._collectors[ref.cls].GRACE
            if seen is None:
                rows.append(self._row(ref, "mark", "no workspace record names it", now))
            elif now - seen >= grace:
                rows.append(self._sweep(ref, apply=apply, now=now))
            else:
                waiting.append(Waiting(ref, first_seen=seen, sweepable_at=seen + grace))
        for ref in self._store.marked() - candidates:
            if classes is None or ref.cls in classes:
                rows.append(self._row(ref, "clear", "no longer present or now entitled", now))
        standing: dict[ResourceClass, int] = {}
        for row in rows:
            if row.action != "clear" and not (row.action == "sweep" and row.outcome == "ok"):
                standing[row.ref.cls] = standing.get(row.ref.cls, 0) + 1
        for wait in waiting:
            standing[wait.ref.cls] = standing.get(wait.ref.cls, 0) + 1
        self._store.record(rows, standing=standing)
        for row in rows:
            self._emit(row)
        return PassReport(rows=tuple(rows), standing=standing, waiting=tuple(waiting))

    def _sweep(self, ref: ResourceRef, *, apply: bool, now: datetime) -> ReaperRow:
        reason = f"unentitled for at least {self._collectors[ref.cls].GRACE}"
        if not apply:
            return self._row(ref, "report", reason, now)
        with paths.exclusive_lock(self._workspaces.path):
            self._workspaces.invalidate()
            if ref not in Ledger.candidates([ref], self._workspaces.load_all()):
                return self._row(ref, "clear", "a workspace record claimed it", now)
            try:
                self._collectors[ref.cls].remove(ref)
            except GroveError as exc:
                logger.warning("reaper: could not remove {} {}: {}", ref.cls, ref.key, exc)
                return self._row(ref, "sweep", reason, now, error=str(exc))
        return self._row(ref, "sweep", reason, now)

    @staticmethod
    def _row(
        ref: ResourceRef, action: Action, reason: str, now: datetime, *, error: str = ""
    ) -> ReaperRow:
        return ReaperRow(
            ref=ref,
            action=action,
            reason=reason,
            outcome="failed" if error else "ok",
            error=error,
            at=now,
        )

    def _emit(self, row: ReaperRow) -> None:
        """One span per action; best-effort, so telemetry can never fail a pass."""
        if self._sink is None:
            return
        attributes: dict[str, str] = {
            "grove.reaper.class": row.ref.cls,
            "grove.reaper.resource": row.ref.key,
            "grove.reaper.action": row.action,
            "grove.reaper.outcome": row.outcome,
            "grove.reaper.reason": row.reason,
        }
        if len(row.ref.workspace_id) == 32:
            attributes[GroveIdentityAttr.WORKSPACE_ID] = row.ref.workspace_id
        if row.error:
            attributes["grove.reaper.error"] = row.error
        try:
            self._sink(
                SpanRecord(
                    trace_id=int.from_bytes(secrets.token_bytes(16)),
                    span_id=int.from_bytes(secrets.token_bytes(8)),
                    parent_span_id=None,
                    name=f"reaper {row.action} {row.ref.cls}",
                    kind="tool",
                    start_time=row.at,
                    end_time=row.at,
                    attributes=attributes,
                )
            )
        except Exception as exc:
            logger.debug("reaper: span not emitted: {}", exc)


__all__ = ["PassReport", "Reaper"]
