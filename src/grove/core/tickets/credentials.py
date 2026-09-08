"""Own the ticket credential snapshot for one repository configuration.

A daemon keeps ticket providers for its lifetime, while an external credential
source is an I/O boundary. ``TicketEnv`` therefore resolves that source once,
then serves an immutable, generation-numbered snapshot to every provider. An
explicit :meth:`invalidate` or :meth:`refresh` replaces it; a configured file
also invalidates itself when its filesystem signature changes. Commands have no
implicit change feed and refresh only when explicitly requested.

Config carries names and locations only. Values stay inside the snapshot and
never appear in this module's repr, errors, or logs.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Lock
from types import MappingProxyType

from grove.core.config import TicketsConfig
from grove.core.env_source import EnvSource
from grove.core.errors import EnvSourceError, TicketProviderError


@dataclass(frozen=True, slots=True, repr=False)
class _CredentialSnapshot:
    """One atomically published credential outcome, never a mutable mapping."""

    generation: int
    values: Mapping[str, str] | None
    failure: str | None
    file_signature: tuple[int, int, int, int, int] | None


class TicketEnv(Mapping[str, str]):
    """A repository-local credential mapping with explicit snapshot ownership.

    The configured ticket source wins over the live process environment for a
    key it contains. A source is resolved on the first read, then ordinary
    mapping and provider capability reads have no subprocess side effect. An
    explicit :meth:`invalidate` drops the old result before the next resolution
    (including a formerly valid token), so refresh and revocation fail closed.

    A file source has a cheap change detector at consumption: a changed,
    replaced, created, or deleted file starts one replacement resolution. An
    arbitrary command has no such detector by design; its owner must call
    :meth:`invalidate` or :meth:`refresh` after rotating it.
    """

    def __init__(
        self,
        cfg: TicketsConfig,
        *,
        repo_root: Path | None = None,
        base: Mapping[str, str] | None = None,
    ) -> None:
        self._cfg = cfg
        self._repo_root = repo_root
        # Hold the live object, not a copy: the no-source fallback intentionally
        # retains the historic process-environment behaviour without snapshotting
        # values that TicketEnv does not own.
        self._base: Mapping[str, str] = base if base is not None else os.environ
        self._condition = Condition(Lock())
        self._snapshot: _CredentialSnapshot | None = None
        self._generation = 0
        self._invalidated = self._has_source()
        self._resolving = False
        self._invalidate_after_resolution = False
        self._closed = False

    def __repr__(self) -> str:
        """Expose state only; either mapping could contain credentials."""
        with self._condition:
            return (
                "TicketEnv("
                f"source={'yes' if self._has_source() else 'no'}, "
                f"generation={self._generation}, "
                f"resolving={'yes' if self._resolving else 'no'}"
                ")"
            )

    @property
    def generation(self) -> int:
        """The latest published source outcome; zero means none has been read."""
        with self._condition:
            return self._generation

    def invalidate(self) -> None:
        """Discard the current source result before its next consumer reads it.

        An edge arriving during a resolution requests one follow-up replacement;
        concurrent rotation notifications coalesce rather than forking commands.
        """
        if not self._has_source():
            return
        with self._condition:
            if self._resolving:
                self._invalidate_after_resolution = True
            else:
                self._invalidate_locked()

    def refresh(self) -> int:
        """Resolve and atomically publish the source now, returning its generation.

        Concurrent refresh callers join one in-flight replacement. A failed
        refresh publishes a denied outcome instead of leaving a prior token live,
        so callers fail closed until a new invalidation arrives.
        """
        with self._condition:
            if self._closed:
                raise TicketProviderError("ticket credentials are closed")
        if not self._has_source():
            return self.generation
        with self._condition:
            if self._closed:
                raise TicketProviderError("ticket credentials are closed")
            if self._resolving:
                self._condition.wait_for(lambda: not self._resolving)
                snapshot = self._snapshot
                if snapshot is None or snapshot.failure is not None:
                    raise TicketProviderError(
                        snapshot.failure if snapshot else "ticket credentials are closed"
                    )
                return snapshot.generation
            self._invalidate_locked()
            self._resolving = True
            file_signature = self._file_signature()
        self._resolve_and_publish(file_signature)
        return self.generation

    def close(self) -> None:
        """Forget owned credentials and permanently refuse later source reads."""
        with self._condition:
            self._closed = True
            self._snapshot = None
            self._invalidated = True
            self._condition.notify_all()

    def __getitem__(self, key: str) -> str:
        source = self._source_values()
        if key in source:
            return source[key]
        return self._base[key]

    def __iter__(self) -> Iterator[str]:
        return iter({**self._base, **self._source_values()})

    def __len__(self) -> int:
        return len({**self._base, **self._source_values()})

    def _has_source(self) -> bool:
        return bool(self._cfg.env_file or self._cfg.env_command)

    def _source_values(self) -> Mapping[str, str]:
        """Return the one current source snapshot, resolving it at most once.

        The lock owns state transitions, never the slow source resolution. All
        contenders either publish one immutable replacement or wait for it; a
        source failure is published too, rather than causing every capability
        render to repeat a failing command.
        """
        if not self._has_source():
            return MappingProxyType({})
        with self._condition:
            while True:
                if self._closed:
                    raise TicketProviderError("ticket credentials are closed")
                self._invalidate_for_file_change_locked()
                snapshot = self._snapshot
                if not self._invalidated and snapshot is not None:
                    if snapshot.failure is not None:
                        raise TicketProviderError(snapshot.failure)
                    assert snapshot.values is not None
                    return snapshot.values
                if self._resolving:
                    # A concurrent first-read/refresh owns the one source call.
                    # Its published outcome, including failure, answers us too.
                    self._condition.wait()
                    continue
                self._resolving = True
                file_signature = self._file_signature()
                break

        return self._resolve_and_publish(file_signature)

    def _resolve_and_publish(
        self, file_signature: tuple[int, int, int, int, int] | None
    ) -> Mapping[str, str]:
        """Resolve outside the state lock, then publish one immutable outcome."""
        try:
            root = self._repo_root if self._repo_root is not None else Path.cwd()
            values = MappingProxyType(dict(EnvSource.resolve(self._cfg, repo_root=root).values))
            failure: str | None = None
        except EnvSourceError as exc:
            values = None
            failure = f"ticket credentials could not be resolved: {exc}"
        except Exception:
            # An arbitrary resolver failure can expose values in its text. The
            # snapshot must report only a stable, non-secret denial and release
            # all single-flight waiters.
            values = None
            failure = "ticket credentials could not be resolved"

        with self._condition:
            self._generation += 1
            closed = self._closed
            self._snapshot = (
                None
                if closed
                else _CredentialSnapshot(
                    generation=self._generation,
                    values=values,
                    failure=failure,
                    # Record the signature observed before the read. If a writer
                    # changed the file during it, the next consumer sees the newer
                    # signature and replaces this possibly mixed observation.
                    file_signature=file_signature,
                )
            )
            invalidated = self._invalidate_after_resolution or closed
            self._invalidated = invalidated
            self._invalidate_after_resolution = False
            self._resolving = False
            self._condition.notify_all()
            snapshot = self._snapshot

        if closed:
            raise TicketProviderError("ticket credentials are closed")
        if invalidated:
            return self._source_values()
        assert snapshot is not None
        if snapshot.failure is not None:
            raise TicketProviderError(snapshot.failure)
        assert snapshot.values is not None
        return snapshot.values

    def _invalidate_locked(self) -> None:
        """Mark the last result stale while holding the state lock.

        The prior snapshot stays private until the replacement resolves, so a
        concurrent reader cannot start a duplicate source call. It is never
        returned after this edge: ``_invalidated`` wins the consumption check.
        """
        self._invalidated = True

    def _invalidate_for_file_change_locked(self) -> None:
        """Turn a detected file edge into the same explicit replacement path."""
        snapshot = self._snapshot
        if (
            self._cfg.env_file
            and snapshot is not None
            and (snapshot.file_signature != self._file_signature())
        ):
            self._invalidate_locked()

    def _file_signature(self) -> tuple[int, int, int, int, int] | None:
        """Identify file replacement without reading a secret value.

        ``None`` is also a useful state: a missing file's failed result is
        cached until creation changes the signature, avoiding a subprocess-like
        failure storm on ordinary provider capability reads.
        """
        if not self._cfg.env_file:
            return None
        path = Path(self._cfg.env_file).expanduser()
        if not path.is_absolute():
            path = (self._repo_root if self._repo_root is not None else Path.cwd()) / path
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)


__all__ = ["TicketEnv"]
