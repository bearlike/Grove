"""How a lifecycle verb runs: off the loop, serialized per workspace, bounded pool.

Before the verbs moved to a thread pool, the single-threaded event loop was
an *implicit mutex* over the whole lifecycle surface — no two verbs could ever
interleave, because only one thread ran them. Moving them off the loop bought
responsiveness and deleted that invariant in the same stroke; this module is
where it is paid back explicitly.

The exposure is NOT the store: ``JsonWorkspaceStore.save()``/``delete()`` already
hold ``paths.exclusive_lock`` across the whole read-modify-write, and ``flock``
on a per-call fd excludes threads as well as processes. It is the span *between*
the read and the save, where a verb mutates git worktrees, tmux sessions and
container runtimes: a ``kill`` removing a worktree while a ``respawn`` is halfway
through ``devcontainer up`` leaves a half-torn-down container and a store record
describing neither state. No file lock can see that.

Two properties are load-bearing and easy to lose in a refactor:

- **The key is the workspace id, never a global.** Verbs on different workspaces
  MUST still run concurrently — that is the entire reason the pool exists.
- **The wait happens on the LOOP, not in the pool.** An ``asyncio.Lock`` held by
  a suspended coroutine costs nothing; a ``threading.Lock`` blocked inside a pool
  thread consumes one of the few workers, so a queue of verbs on one workspace
  could starve every other workspace out of the pool it was given to protect.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

T = TypeVar("T")

# Size of the dedicated lifecycle thread pool (see the module docstring and
# `_LifecycleRunner` for WHY lifecycle work does not share the default
# executor). High enough that a fleet operating on several workspaces at once
# doesn't serialize; low enough that it can never become N simultaneous
# `devcontainer up` builds pegging the host, which is what an unbounded pool
# would permit.
_LIFECYCLE_EXECUTOR_WORKERS = 8


@dataclass(slots=True)
class _KeyedLockEntry:
    """One key's lock plus the number of coroutines that still need it."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    holders: int = 0


class _KeyedLocks:
    """Mutual exclusion per key, with no entry left behind for a dead key.

    A plain ``dict[str, asyncio.Lock]`` would be a leak: workspace ids are
    created and destroyed for the life of the daemon, so an entry per id ever
    seen grows without bound. Reference counting is the cheap fix — an entry
    exists exactly while someone holds or awaits it, and evaporates after the
    last release, with no reaper task to own.

    The bookkeeping needs no mutex of its own: every mutation below happens
    between two ``await`` points on the single-threaded loop, so no other
    coroutine can observe a half-updated entry.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _KeyedLockEntry] = {}

    def __len__(self) -> int:
        """Live entries — the assertion a leak test makes."""
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key: str | None) -> AsyncIterator[None]:
        """Hold ``key``'s lock for the block. ``None`` means "nothing to serialize"."""
        if key is None:
            yield
            return
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = _KeyedLockEntry()
        # Claimed BEFORE the first await, so a waiter can never have its entry
        # dropped out from under it by a releaser that ran while it was queued.
        entry.holders += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.holders -= 1
            if entry.holders == 0:
                del self._entries[key]


class _LifecycleRunner:
    """Runs one lifecycle verb: off the loop, on its own pool, serialized per workspace.

    The pool is deliberately NOT the loop's default executor that every read
    route uses. That default (sized ``min(32, cpu + 4)``, i.e. 8 threads on a
    4-core host) is the render path: every workspace/session read, each focused
    pane capture, the 2s activity poll behind the SSE stream, container image
    prebuilds. Lifecycle calls are the only ones measured in MINUTES (a
    container create runs a full ``devcontainer up``) and a fleet issues them in
    bursts, so sharing the pool would trade a total event-loop stall for pool
    starvation — the same freeze, visible to every other repo's dashboard.
    Threads are lazy, so this costs nothing until the first lifecycle request.
    """

    def __init__(self, *, max_workers: int = _LIFECYCLE_EXECUTOR_WORKERS) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="grove-lifecycle"
        )
        self._locks = _KeyedLocks()

    async def run(self, key: str | None, fn: Callable[..., T], *args: Any) -> T:
        """Await ``fn(*args)`` on the pool, exclusive against other verbs on ``key``.

        ``key`` is the workspace id the verb mutates, or ``None`` for a verb that
        targets no existing workspace — ``create``, whose id is minted *by* the
        call, so there is nothing for a second create to interleave with (they
        address disjoint workspaces by construction, and the store's own file
        lock covers the record insert). Keying create globally would serialize
        exactly the case the pool was added for.
        """
        loop = asyncio.get_running_loop()
        async with self._locks.hold(key):
            return await loop.run_in_executor(self._executor, fn, *args)

    def shutdown(self) -> None:
        """Drop queued verbs, let running ones finish, never block shutdown.

        ``wait=False`` so shutdown is never held hostage by an in-flight
        provision (a ``devcontainer up`` can run for minutes);
        ``cancel_futures=True`` drops anything still queued, which is the only
        part we can honestly abandon — work already running in a thread has side
        effects on disk and in the container runtime, so it is left to finish
        rather than half-torn-down.
        """
        self._executor.shutdown(wait=False, cancel_futures=True)
