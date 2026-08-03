"""The durable marker: which tickets Grove has already been handed.

This module exists because of one loop. The outbound half assigns the bot to
every ticket a Grove workspace holds; the inbound half picks up every ticket
assigned to the bot. Left alone, Grove assigns → Grove sees an assigned ticket →
Grove starts another workspace → forever. "Has never been handed over before"
must therefore be a **durable marker that is not derived from current state**,
because current state is precisely what Grove mutates. A prior assignment a
human later REMOVED must still count as handed over, which rules out every
answer of the form "look at the tracker".

**Why not the sticky comment's ``STICKY_MARKER``.** It is on the thread, it is
durable, and it was the obvious candidate — and it is wrong, fatally rather than
partially: that marker only exists where ``issueops.enabled`` turned the status
publisher on AND a flush actually reached the forge. A deployment running pickup
with the publisher off would find no marker on any thread and re-pick up every
ticket on every tick, spawning workspaces without bound. A guard whose failure
mode is an unbounded loop must not depend on a feature that can be switched off
underneath it.

So the marker is local: one small JSON file under the state dir, written with
the same discipline :class:`~grove.core.store.JsonWorkspaceStore` uses —
:func:`grove.core.paths.write_atomic` for publication and
:func:`grove.core.paths.exclusive_lock` held across the whole read-modify-write,
because the daemon's poll and a `grove tickets` CLI verb are two processes over
one file.

Two failure modes are deliberate. An unreadable or corrupt file **raises**
rather than reading as empty: an empty read here is indistinguishable from "no
ticket has ever been handed over", which is the loop again — so the poller
treats a raise as "skip this tick entirely" and says so. And the log **never
evicts**: an evicted row is a ticket that can be picked up a second time, and
one row per ticket ever handed over is not a size problem.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from grove.core import paths
from grove.core.contracts.tickets import TicketProviderName
from grove.core.errors import GroveError

_VERSION = 1

HandoverSource = Literal["poll", "command"]
"""How a ticket came to Grove: the assignee poll saw it, or a human ran the
command. Recorded because the two are answerable differently when somebody asks
why a workspace exists — not because any code branches on it."""


@dataclass(frozen=True, slots=True)
class HandoverKey:
    """What identifies a handed-over ticket: provider + repo + id.

    Scoped by ``owner``/``repo`` and not by ticket id alone, because a bare
    number means different things on two repos of one forge — the same reason
    the issue-ops engine resolves repo+ticket rather than scanning fleet-wide.
    """

    provider: TicketProviderName
    owner: str
    repo: str
    ticket_id: str

    @property
    def wire(self) -> str:
        """The stable string this key is stored and logged under."""
        return f"{self.provider}:{self.owner}/{self.repo}#{self.ticket_id}"


@dataclass(frozen=True, slots=True)
class HandoverEntry:
    """One durable row: this ticket was given to Grove, when, how, and to whom.

    ``workspace_id`` is filled in AFTER the create returns and stays ``None``
    when the create failed — deliberately, because the claim is written first
    (see :meth:`HandoverLog.claim`). A row with no workspace is the honest record
    of "Grove took this ticket and did not finish starting", which is a thing a
    human wants to see rather than a state to clean up automatically.
    """

    key: HandoverKey
    claimed_at: datetime
    source: HandoverSource
    workspace_id: str | None = None


class HandoverLog:
    """Append-mostly JSON log of every ticket Grove has been handed.

    One file for the whole host, keyed by :attr:`HandoverKey.wire`. Reads are
    unlocked (the atomic rename means a reader sees a whole file or the previous
    whole file); every mutation holds :func:`paths.exclusive_lock` across its
    read-modify-write so a poll tick and a CLI verb cannot lose each other's row.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else paths.user_handover_path()

    @property
    def path(self) -> Path:
        return self._path

    # ─── reads ──────────────────────────────────────────────────────────────

    def entries(self) -> list[HandoverEntry]:
        """Every row, oldest claim first. Raises on a file it cannot trust."""
        return sorted(self._load().values(), key=lambda e: e.claimed_at)

    def keys(self) -> set[str]:
        """The wire keys of every handed-over ticket — the poll's eligibility set."""
        return set(self._load())

    def contains(self, key: HandoverKey) -> bool:
        return key.wire in self._load()

    # ─── mutations ──────────────────────────────────────────────────────────

    def claim(self, key: HandoverKey, *, source: HandoverSource, now: datetime) -> bool:
        """Record the handover, returning False if it was already recorded.

        **Called BEFORE the create, never after**, and the ordering is the whole
        point: claiming first can at worst lose one pickup (Grove crashes between
        the claim and the create, and a human re-hands the ticket over), while
        creating first loops without bound on any crash in that same window —
        the next tick sees an assigned ticket with no marker and starts another
        workspace. Cheap failure over unbounded failure.

        The return value makes the claim itself the race arbiter: two writers
        serialize on the lock, and only the first gets ``True``.
        """
        with paths.exclusive_lock(self._path):
            records = self._load()
            if key.wire in records:
                return False
            records[key.wire] = HandoverEntry(key=key, claimed_at=now, source=source)
            self._write(records)
        return True

    def record_workspace(self, key: HandoverKey, workspace_id: str) -> None:
        """Attach the started workspace to an existing claim. No-op if unclaimed."""
        with paths.exclusive_lock(self._path):
            records = self._load()
            entry = records.get(key.wire)
            if entry is None:
                return
            records[key.wire] = HandoverEntry(
                key=entry.key,
                claimed_at=entry.claimed_at,
                source=entry.source,
                workspace_id=workspace_id,
            )
            self._write(records)

    # ─── serialization ──────────────────────────────────────────────────────

    def _load(self) -> dict[str, HandoverEntry]:
        if not self._path.exists():
            return {}
        try:
            with self._path.open(encoding="utf-8") as fh:
                data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise GroveError(f"corrupt handover log at {self._path}: {exc}") from exc
        except OSError as exc:
            raise GroveError(f"cannot read handover log at {self._path}: {exc}") from exc
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            raise GroveError(
                f"handover log at {self._path} has version {data.get('version')!r} "
                f"(expected {_VERSION}); refusing to treat it as empty"
            )
        raw = data.get("handovers")
        if not isinstance(raw, dict):
            raise GroveError(f"`handovers` must be an object in {self._path}")
        return {k: self._decode(v) for k, v in raw.items() if isinstance(v, dict)}

    def _write(self, records: dict[str, HandoverEntry]) -> None:
        payload = {
            "version": _VERSION,
            "handovers": {k: self._encode(v) for k, v in records.items()},
        }
        paths.write_atomic(self._path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _encode(entry: HandoverEntry) -> dict[str, Any]:
        return {
            "provider": entry.key.provider,
            "owner": entry.key.owner,
            "repo": entry.key.repo,
            "ticket_id": entry.key.ticket_id,
            "claimed_at": entry.claimed_at.isoformat(),
            "source": entry.source,
            "workspace_id": entry.workspace_id,
        }

    @staticmethod
    def _decode(raw: dict[str, Any]) -> HandoverEntry:
        claimed = raw.get("claimed_at")
        try:
            when = datetime.fromisoformat(str(claimed))
        except ValueError as exc:
            raise GroveError(f"handover row has an unreadable claimed_at {claimed!r}") from exc
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        source: HandoverSource = "poll" if raw.get("source") != "command" else "command"
        return HandoverEntry(
            key=HandoverKey(
                provider=raw.get("provider", "gitea"),
                owner=str(raw.get("owner", "")),
                repo=str(raw.get("repo", "")),
                ticket_id=str(raw.get("ticket_id", "")),
            ),
            claimed_at=when,
            source=source,
            workspace_id=raw.get("workspace_id") or None,
        )


__all__ = ["HandoverEntry", "HandoverKey", "HandoverLog", "HandoverSource"]
