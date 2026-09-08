"""Single-file JSON persistence for WorkspaceState records.

One global `state.json` (path from `paths.user_state_path()`) holds every
workspace, keyed internally by workspace id. Filtering by repo is a
public method, so callers stay agnostic of the storage layout.

Schema is versioned (currently `1`) — when the field set changes, bump the
version and write a migration step here.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core import paths
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.diagrams import DiagramSessionView
from grove.core.contracts.tickets import TicketRef
from grove.core.errors import DiagramConflict, GroveError, WorkspaceNotFound
from grove.core.workspace import (
    PERSISTED_STATUSES,
    BranchProvenance,
    InitStatus,
    Placement,
    ProvisionStatus,
    Runtime,
    TranscriptContext,
    WorkspaceState,
    WorkspaceStatus,
)
from grove.core.workspace_history import WorkspaceHistoryStore

_VERSION = 1

# Legacy on-disk status values that have been removed from the enum but may
# still appear in older state.json files. Each maps to the persisted intent
# that originally produced it; reconciliation at read time will re-derive the
# user-visible status (e.g. legacy `stale` → RUNNING intent → OFFLINE on read).
_LEGACY_STATUS_ALIASES: dict[str, WorkspaceStatus] = {
    "stale": WorkspaceStatus.RUNNING,
}


@dataclass(frozen=True, slots=True)
class _FileSignature:
    """Identity plus contents-relevant metadata for one published state file."""

    device: int
    inode: int
    modified_ns: int
    size: int

    @classmethod
    def read(cls, path: Path) -> _FileSignature | None:
        try:
            return cls.from_stat(path.stat())
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise GroveError(f"cannot stat state file at {path}: {exc}") from exc

    @classmethod
    def from_stat(cls, stat: os.stat_result) -> _FileSignature:
        return cls(
            device=stat.st_dev,
            inode=stat.st_ino,
            modified_ns=stat.st_mtime_ns,
            size=stat.st_size,
        )


@dataclass(slots=True)
class _Snapshot:
    """One parsed generation with constant-time workspace and repository lookups."""

    signature: _FileSignature | None
    by_id: dict[str, WorkspaceState]
    ids_by_repo: dict[str, list[str]]

    @classmethod
    def empty(cls, signature: _FileSignature | None = None) -> _Snapshot:
        return cls(signature=signature, by_id={}, ids_by_repo={})


class JsonWorkspaceStore:
    """Atomic, single-file JSON store for workspaces.

    Writes go through `paths.write_atomic` (private temp name + `os.replace`),
    so a concurrent writer — the daemon, a `grove` CLI verb and the TUI are
    three processes over this one file — can never publish a half-written
    record. Each mutation additionally holds `paths.exclusive_lock` across its
    whole read-modify-write, so two overlapping saves serialize instead of
    resolving last-writer-wins: without it, both read the same records, each
    added its own, and the second rename published a file missing the first
    one's workspace entirely.

    Reads (`load_all` / `get` / `for_repo`) take no lock and need none: the
    atomic rename means a reader either sees the whole previous file or the
    whole next one, never a blend. Each instance keeps a parsed, indexed
    snapshot keyed by file signature; an unchanged generation skips JSON
    parsing, while a different process's atomic replacement or deletion reloads
    before returning data.
    """

    def __init__(
        self,
        path: Path | None = None,
        *,
        history: WorkspaceHistoryStore | None = None,
    ) -> None:
        self._path = path if path is not None else paths.user_state_path()
        # Injected for tests; built lazily otherwise so constructing a store
        # never touches the history file. `save`/`delete` are the ONE chokepoint
        # every title assignment and every teardown passes through, which is why
        # the recorder lives here rather than in each manager verb — a future
        # verb cannot forget what it does not have to remember.
        self._history = history
        self._history_resolved = history is not None
        # Cache mutation is thread-local coordination only. It is always taken
        # before the process-wide sidecar file lock, preventing an in-flight
        # reader from publishing an older descriptor after a local write.
        self._snapshot_lock = threading.RLock()
        self._snapshot: _Snapshot | None = None

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> list[WorkspaceState]:
        """Return independent states from the latest on-disk generation.

        The stat signature catches another process's atomic replacement or
        deletion; unchanged generations reuse the parsed, indexed snapshot.
        Corruption remains loud exactly as it was before caching.
        """
        return [copy.deepcopy(state) for state in self._current_snapshot().by_id.values()]

    def get(self, workspace_id: str) -> WorkspaceState:
        state = self._current_snapshot().by_id.get(workspace_id)
        if state is None:
            raise WorkspaceNotFound(workspace_id)
        return copy.deepcopy(state)

    def invalidate(self) -> None:
        """Forget the local snapshot when an external watcher observes a write."""
        with self._snapshot_lock:
            self._snapshot = None

    def save(self, state: WorkspaceState) -> None:
        """Insert or replace by id.

        Refuses to persist computed statuses (ACTIVE / IDLE / OFFLINE /
        ORPHANED) — those are derived at read time, never written. Catching
        the bad write here means a manager-level bug can't pollute state.json.
        """
        if state.status not in PERSISTED_STATUSES:
            raise GroveError(
                f"refusing to persist computed status {state.status!r} for {state.id}; "
                f"only {sorted(s.value for s in PERSISTED_STATUSES)} round-trip"
            )
        with self._snapshot_lock, paths.exclusive_lock(self._path):
            records = dict(self._read_snapshot().by_id)
            current = records.get(state.id)
            previous_ticket_refs = current.ticket_refs if current is not None else ()
            # Diagram descriptors are collaboration generations. A caller that
            # read an ordinary state before an editor opens/stops must not be
            # able to erase or revive that generation on its later whole-record
            # save. New records retain their supplied initial descriptor.
            if current is not None:
                state = replace(state, diagram=current.diagram)
            records[state.id] = state
            self._write(records.values())
        # AFTER the lock and after the state is durable: the history is a
        # secondary record, so it must never hold `state.json`'s lock (three
        # processes contend for it) and must never be the reason a save fails.
        self._record_name(state)
        self._record_ticket_transition(state.id, previous_ticket_refs, state.ticket_refs)

    def update_diagram_descriptor(
        self,
        workspace_id: str,
        *,
        expected: DiagramSessionView | None,
        replacement: DiagramSessionView,
    ) -> WorkspaceState:
        """Atomically replace one workspace's expected diagram descriptor.

        Diagram descriptors have their own generation fence. Updating under the
        state-store lock preserves every unrelated field written after the
        manager read its copy, while the expected descriptor refuses a competing
        open or stop rather than silently replacing it.
        """
        with self._snapshot_lock, paths.exclusive_lock(self._path):
            records = dict(self._read_snapshot().by_id)
            current = records.get(workspace_id)
            if current is None:
                raise WorkspaceNotFound(workspace_id)
            if current.diagram != expected:
                raise DiagramConflict("diagram descriptor changed while updating; retry")
            updated = replace(current, diagram=replacement, updated_at=datetime.now(tz=UTC))
            records[workspace_id] = updated
            self._write(records.values())
            return updated

    def delete(self, workspace_id: str) -> None:
        with self._snapshot_lock, paths.exclusive_lock(self._path):
            records = dict(self._read_snapshot().by_id)
            if workspace_id not in records:
                raise WorkspaceNotFound(workspace_id)
            records.pop(workspace_id)
            self._write(records.values())
        # The moment this record is gone, the history store is the ONLY thing
        # that can still name this workspace — which is the whole reason it
        # exists. Tombstone rather than delete, so a usage row can resolve its
        # title and a client can say the workspace is gone.
        try:
            self._history_store().mark_deleted(workspace_id)
        except Exception as exc:
            logger.debug("workspace history not tombstoned for {}: {}", workspace_id, exc)

    @property
    def history(self) -> WorkspaceHistoryStore:
        """The durable name/progress record this store writes to.

        Public so a process holding several readers of the same data — the
        daemon serves `GET /workspaces/{id}/history` beside this store's own
        writes — can share ONE instance rather than opening a second connection
        to the same file. Two instances are not a correctness bug in production
        (SQLite in WAL mode handles concurrent connections, and both resolve the
        same default path), but they are two caches of one truth, and a test
        that injects one of them then reads through the other sees a tombstone
        that never arrived. Reading this resolves the lazy default, so a caller
        that only wants to know whether one exists must not touch it.
        """
        return self._history_store()

    def _history_store(self) -> WorkspaceHistoryStore:
        """The durable name/progress record, built on first use.

        Resolved lazily so merely constructing a `JsonWorkspaceStore` — which
        happens in every CLI verb and every test — does not create the file.
        """
        if not self._history_resolved:
            self._history = WorkspaceHistoryStore()
            self._history_resolved = True
        assert self._history is not None
        return self._history

    def _record_name(self, state: WorkspaceState) -> None:
        """Record this workspace's current name, best-effort.

        Swallows everything: a title is worth strictly less than the `create`
        that was assigning it, and the store's own writes already log their
        failures. A `record_name` that raised here would make a secondary
        record able to fail a lifecycle verb.
        """
        try:
            self._history_store().record_name(
                state.id,
                title=state.title,
                description=state.description,
                repo_root=str(state.repo_root),
            )
        except Exception as exc:
            logger.debug("workspace history not recorded for {}: {}", state.id, exc)

    def _record_ticket_transition(
        self,
        workspace_id: str,
        previous: Iterable[TicketRef],
        current: Iterable[TicketRef],
    ) -> None:
        """Record newly attached or kind-corrected tickets after their state save.

        Ticket membership is durable state, unlike the activity tick's phase
        observation. Capturing only the refs that changed makes a repeated
        state replay inert and keeps a read-only poll from advancing
        ``last_seen``.
        """
        try:
            self._history_store().record_ticket_transition(
                workspace_id, tuple(previous), tuple(current)
            )
        except Exception as exc:
            logger.debug("workspace ticket history not recorded for {}: {}", workspace_id, exc)

    def for_repo(self, repo_root: Path) -> list[WorkspaceState]:
        """All workspaces whose `repo_root` matches the given canonical path."""
        snapshot = self._current_snapshot()
        target = str(Path(repo_root).resolve())
        return [
            copy.deepcopy(snapshot.by_id[ws_id]) for ws_id in snapshot.ids_by_repo.get(target, [])
        ]

    def list_repo_roots(self) -> list[Path]:
        """Distinct repo roots across all persisted workspaces.

        Used by the HTTP daemon to enumerate which Managers it needs to
        instantiate when serving ``GET /workspaces`` (multi-repo aggregation).
        """
        return [Path(repo_root) for repo_root in self._current_snapshot().ids_by_repo]

    # ─── internal ──────────────────────────────────────────────────────────

    def _current_snapshot(self) -> _Snapshot:
        with self._snapshot_lock:
            signature = _FileSignature.read(self._path)
            if self._snapshot is None or self._snapshot.signature != signature:
                self._snapshot = self._read_snapshot(signature)
            return self._snapshot

    def _read_snapshot(self, signature: _FileSignature | None = None) -> _Snapshot:
        """Read a complete file generation, bypassing any local cached snapshot."""
        if signature is None:
            signature = _FileSignature.read(self._path)
        if signature is None:
            return _Snapshot.empty()
        try:
            with self._path.open(encoding="utf-8") as fh:
                data = json.load(fh)
                # Atomic replacement changes the path's inode, not this
                # descriptor's. Bind parsed bytes to the descriptor so a
                # replacement mid-read cannot label old data as new.
                signature = _FileSignature.from_stat(os.fstat(fh.fileno()))
        except FileNotFoundError:
            # An atomic replacer may delete between stat and open. Its next
            # generation is the empty store, not stale cached content.
            return _Snapshot.empty()
        except json.JSONDecodeError as exc:
            raise GroveError(f"corrupt state file at {self._path}: {exc}") from exc
        except OSError as exc:
            raise GroveError(f"cannot read state file at {self._path}: {exc}") from exc

        if not isinstance(data, dict):
            raise GroveError(f"unexpected state shape at {self._path}")
        if data.get("version") != _VERSION:
            raise GroveError(
                f"state version {data.get('version')!r} not supported (expected {_VERSION})"
            )
        raw = data.get("workspaces", {})
        if not isinstance(raw, dict):
            raise GroveError(f"`workspaces` must be an object in {self._path}")

        by_id: dict[str, WorkspaceState] = {}
        for record in raw.values():
            state = self._deserialize(record)
            by_id[state.id] = state
        ids_by_repo: dict[str, list[str]] = {}
        for workspace_id, state in by_id.items():
            ids_by_repo.setdefault(state.repo_root, []).append(workspace_id)
        return _Snapshot(signature=signature, by_id=by_id, ids_by_repo=ids_by_repo)

    def _write(self, states: Iterable[WorkspaceState]) -> None:
        serialized = {s.id: self._serialize(s) for s in states}
        payload: dict[str, Any] = {
            "version": _VERSION,
            "workspaces": serialized,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        paths.write_atomic(self._path, text)
        by_id = {
            workspace_id: self._deserialize(record) for workspace_id, record in serialized.items()
        }
        ids_by_repo: dict[str, list[str]] = {}
        for workspace_id, state in by_id.items():
            ids_by_repo.setdefault(state.repo_root, []).append(workspace_id)
        self._snapshot = _Snapshot(
            signature=_FileSignature.read(self._path), by_id=by_id, ids_by_repo=ids_by_repo
        )
        logger.debug("state saved: {} workspace(s) → {}", len(serialized), self._path)

    @staticmethod
    def _serialize(state: WorkspaceState) -> dict[str, Any]:
        data = asdict(state)
        data["status"] = state.status.value
        data["created_at"] = state.created_at.isoformat()
        data["updated_at"] = state.updated_at.isoformat()
        data["paused_at"] = state.paused_at.isoformat() if state.paused_at else None
        data["share_expires_at"] = (
            state.share_expires_at.isoformat() if state.share_expires_at else None
        )
        data["init_status"] = state.init_status.value if state.init_status else None
        data["branch_provenance"] = state.branch_provenance.value
        data["placement"] = state.placement.value
        data["runtime"] = state.runtime.value
        data["provision_status"] = state.provision_status.value if state.provision_status else None
        # `asdict` leaves the Pydantic TicketRefs as model instances (it only
        # recurses dataclasses), so serialize them to plain JSON dicts here —
        # the same explicit-field treatment the enums/datetimes get.
        data["ticket_refs"] = [r.model_dump(mode="json") for r in state.ticket_refs]
        # Same treatment for the container identity (Pydantic, so `asdict` left
        # it as a model instance). Persisting it is what makes teardown possible
        # at all: a container whose id is not on disk is a container nothing can
        # ever find again.
        data["container"] = state.container.model_dump(mode="json") if state.container else None
        data["diagram"] = state.diagram.model_dump(mode="json") if state.diagram else None
        return data

    @staticmethod
    def _deserialize(data: dict[str, Any]) -> WorkspaceState:
        # `.get()` for fields added after v1 shipped — older state.json files
        # written before init_status / branch_provenance existed must still
        # load cleanly. branch_provenance defaults to GROVE_CREATED for
        # legacy records, matching historical behavior ("Grove always
        # created the branch").
        init_status_raw = data.get("init_status")
        provision_status_raw = data.get("provision_status")
        return WorkspaceState(
            id=data["id"],
            title=data["title"],
            repo_root=data["repo_root"],
            branch=data["branch"],
            base_branch=data["base_branch"],
            worktree_path=data["worktree_path"],
            tmux_session=data["tmux_session"],
            agent_name=data["agent_name"],
            status=_decode_status(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            paused_at=(
                datetime.fromisoformat(data["paused_at"]) if data.get("paused_at") else None
            ),
            error_detail=data.get("error_detail"),
            description=data.get("description"),
            init_status=InitStatus(init_status_raw) if init_status_raw else None,
            init_duration_ms=data.get("init_duration_ms"),
            init_log_path=data.get("init_log_path"),
            # `.get()` — absent on every record written before the creation
            # anchor existed. None is the honest answer for those: the commit
            # they started from was never observed and cannot be recovered, so
            # the "since created" reads fall back to `base_branch` rather than
            # inventing a baseline.
            base_commit=data.get("base_commit"),
            branch_provenance=BranchProvenance(
                data.get("branch_provenance", BranchProvenance.GROVE_CREATED.value)
            ),
            placement=Placement(data.get("placement", Placement.WORKTREE.value)),
            # `.get()` — absent on records written before nested-project cwd
            # existed; "" means the agent starts at the worktree root,
            # the historical shape.
            project_subpath=data.get("project_subpath", ""),
            # `.get()` — absent on records written before agent-session tracking
            # existed; legacy workspaces simply track no session.
            agent_session_id=data.get("agent_session_id"),
            # `.get()` — absent on records written before agent-kind was
            # persisted; None makes the ActivityService fall back to a config
            # lookup for those legacy records.
            agent_kind=data.get("agent_kind"),
            # `.get()` — absent on records written before ticket association
            # existed; re-validated through the Pydantic model so a corrupt
            # on-disk ref fails loudly here rather than mid-render.
            ticket_refs=[TicketRef.model_validate(r) for r in (data.get("ticket_refs") or [])],
            # `.get()` — absent on every record until a runtime-context override
            # is set; None is the default, current-behavior path for
            # every legacy and non-container workspace.
            transcript_context=_decode_transcript_context(data.get("transcript_context")),
            # `.get(..., "host")` — the identical technique `placement` and
            # `branch_provenance` use: every workspace written before runtime
            # selection existed loads as a host workspace, which is exactly
            # what it is. No migration step, no version bump.
            runtime=Runtime(data.get("runtime", Runtime.HOST.value)),
            # `.get()` — absent on every record written before public sharing
            # existed. None means private, which is what every one of those
            # workspaces is, so the default is also the safe direction.
            share_token=data.get("share_token"),
            # `.get()` — absent on every record written before a share pinned
            # its transcript. None means "not pinned", which is exactly what a
            # link issued before the pin existed is; the reader falls back to
            # the workspace's own primary session rather than inventing a pin.
            share_session_id=data.get("share_session_id"),
            diagram=(
                DiagramSessionView.model_validate(data["diagram"]) if data.get("diagram") else None
            ),
            # A legacy record predates expiring shares, so its links never expire.
            share_expires_at=(
                datetime.fromisoformat(data["share_expires_at"])
                if data.get("share_expires_at")
                else None
            ),
            # `.get(..., False)` — a record written before the first-turn brief
            # existed was never briefed, which is exactly what `False` says.
            brief=bool(data.get("brief", False)),
            native=bool(data.get("native", False)),
            runtime_fallback_reason=data.get("runtime_fallback_reason"),
            provision_status=(
                ProvisionStatus(provision_status_raw) if provision_status_raw else None
            ),
            provision_duration_ms=data.get("provision_duration_ms"),
            provision_log_path=data.get("provision_log_path"),
            provision_started_at=data.get("provision_started_at"),
            # `.get()` — absent on every host-mode and legacy record. Unlike the
            # other optional decodes this one re-validates LOUDLY; see
            # `_decode_container`.
            container=_decode_container(data.get("container")),
            # `.get(..., False)` — a legacy record predates the packaged
            # default config entirely, so it can never have used one.
            runtime_default_config=bool(data.get("runtime_default_config", False)),
        )


def _decode_container(raw: Any) -> ContainerRuntimeState | None:
    """``None`` for a host-mode/legacy record, else the persisted identity.

    The one optional decode here that re-validates LOUDLY rather than degrading
    to ``None`` (the `ticket_refs` precedent, the opposite of
    `_decode_transcript_context`): a lost transcript override falls back to
    still-correct default behavior, but a dropped container record silently
    orphans a REAL container on the host — Grove forgets an identity it is the
    only owner of, and no later teardown can name it. A loud failure the
    operator can fix is the cheaper outcome.
    """
    if raw is None:
        return None
    return ContainerRuntimeState.model_validate(raw)


def _decode_transcript_context(raw: Any) -> TranscriptContext | None:
    """``None`` for a legacy/no-override record, else the persisted override.

    Defensive like every other optional-field decode here: a malformed value
    (wrong shape, missing key) degrades to ``None`` rather than raising —
    losing an override falls back to the current, still-correct default
    behavior instead of breaking `load_all` for the whole store.

    A blank ``config_dir`` decodes to ``None`` too, matching what the only other
    way into this type — ``TranscriptContext.for_launch`` — can ever produce
    (it normalizes a falsy pin away). The two entry points must agree on what a
    falsy value means, because an empty string is NOT inert on the read side:
    it slips past ``transcript_config_dir_scope``'s ``config_dir is None`` guard
    and *sets* the reader's env var to empty, clearing a legitimate ambient pin
    for the duration of the read. Whitespace is stripped for the same reason —
    a ``"   "`` pin would otherwise install an active-but-useless scope. Only
    reachable from a hand-edited state file today; cheap to close anyway.
    """
    if not isinstance(raw, dict):
        return None
    try:
        config_dir = raw["config_dir"].strip()
        agent_cwd = raw["agent_cwd"]
    except (AttributeError, KeyError, TypeError):
        return None
    if not config_dir or not isinstance(agent_cwd, str):
        return None
    return TranscriptContext(config_dir=config_dir, agent_cwd=agent_cwd)


def _decode_status(raw: str) -> WorkspaceStatus:
    """Coerce on-disk status value to a persistable enum.

    Handles legacy values from older state.json files (e.g. `stale` from when
    that was a real enum member) by mapping them to the persisted intent that
    originally produced them. Reconciliation at read time will re-derive the
    user-visible status. Computed values that somehow ended up persisted (a
    bug) coerce to RUNNING so the manager can re-resolve them.
    """
    if raw in _LEGACY_STATUS_ALIASES:
        return _LEGACY_STATUS_ALIASES[raw]
    try:
        status = WorkspaceStatus(raw)
    except ValueError:
        # Unknown value — assume the intent was RUNNING and let reconciliation
        # demote it appropriately.
        return WorkspaceStatus.RUNNING
    if status not in PERSISTED_STATUSES:
        # Computed value snuck onto disk; treat as a RUNNING intent.
        return WorkspaceStatus.RUNNING
    return status
