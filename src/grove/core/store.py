"""Single-file JSON persistence for WorkspaceState records.

One global `state.json` (path from `paths.user_state_path()`) holds every
workspace, keyed internally by workspace id. Filtering by repo is a
public method, so callers stay agnostic of the storage layout.

Schema is versioned (currently `1`) — when the field set changes, bump the
version and write a migration step here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from grove.core import paths
from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.tickets import TicketRef
from grove.core.errors import GroveError, WorkspaceNotFound
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

_VERSION = 1

# Legacy on-disk status values that have been removed from the enum but may
# still appear in older state.json files. Each maps to the persisted intent
# that originally produced it; reconciliation at read time will re-derive the
# user-visible status (e.g. legacy `stale` → RUNNING intent → OFFLINE on read).
_LEGACY_STATUS_ALIASES: dict[str, WorkspaceStatus] = {
    "stale": WorkspaceStatus.RUNNING,
}


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
    whole next one, never a blend.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.user_state_path()

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> list[WorkspaceState]:
        if not self._path.exists():
            return []
        try:
            with self._path.open(encoding="utf-8") as fh:
                data = json.load(fh)
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
        return [self._deserialize(v) for v in raw.values()]

    def get(self, workspace_id: str) -> WorkspaceState:
        for state in self.load_all():
            if state.id == workspace_id:
                return state
        raise WorkspaceNotFound(workspace_id)

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
        with paths.exclusive_lock(self._path):
            records = {s.id: s for s in self.load_all()}
            records[state.id] = state
            self._write(records.values())

    def delete(self, workspace_id: str) -> None:
        with paths.exclusive_lock(self._path):
            records = {s.id: s for s in self.load_all()}
            if workspace_id not in records:
                raise WorkspaceNotFound(workspace_id)
            records.pop(workspace_id)
            self._write(records.values())

    def for_repo(self, repo_root: Path) -> list[WorkspaceState]:
        """All workspaces whose `repo_root` matches the given canonical path."""
        target = str(Path(repo_root).resolve())
        return [s for s in self.load_all() if s.repo_root == target]

    def list_repo_roots(self) -> list[Path]:
        """Distinct repo roots across all persisted workspaces.

        Used by the HTTP daemon to enumerate which Managers it needs to
        instantiate when serving ``GET /workspaces`` (multi-repo aggregation).
        """
        return list({Path(s.repo_root) for s in self.load_all()})

    # ─── internal ──────────────────────────────────────────────────────────

    def _write(self, states: Iterable[WorkspaceState]) -> None:
        serialized = {s.id: self._serialize(s) for s in states}
        payload: dict[str, Any] = {
            "version": _VERSION,
            "workspaces": serialized,
        }
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        paths.write_atomic(self._path, text)
        logger.debug("state saved: {} workspace(s) → {}", len(serialized), self._path)

    @staticmethod
    def _serialize(state: WorkspaceState) -> dict[str, Any]:
        data = asdict(state)
        data["status"] = state.status.value
        data["created_at"] = state.created_at.isoformat()
        data["updated_at"] = state.updated_at.isoformat()
        data["paused_at"] = state.paused_at.isoformat() if state.paused_at else None
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
            # `.get(..., False)` — a record written before the first-turn brief
            # existed was never briefed, which is exactly what `False` says.
            brief=bool(data.get("brief", False)),
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
