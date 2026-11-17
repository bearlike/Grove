"""One durable format for Grove's small mutable stores: checksummed JSONL.

A whole-file JSON document fails as ONE parse, so a single flipped bit anywhere
in it loses every record. Measured on 2026-10-15: one bit inside indentation
whitespace cost 766 watches and the daemon's startup, the third such flip in
that file in three weeks. The unit of corruption has to be the unit of recovery,
so each record is its own line carrying a CRC32 of its own bytes:

    {"format":"grove-records","store":"watches","version":2}
    {"c":"1a2b3c4d","r":{...record...}}

A record line is read one of four ways, and the differences are the point:

- **The CRC fails.** The bytes are not what was written. The file is rewritten
  without the line, the line goes to ``<file>.corrupt``, and the rest load. A
  flipped digit inside a timestamp parses perfectly, so the CRC is the only
  thing that sees it.
- **The CRC passes but the store's decoder refuses.** The bytes are exactly what
  some Grove wrote, just not this one (a watch kind a newer build added). The
  line is kept verbatim and written back, so a newer build never loses a record
  because an older one rewrote the file.
- **The decoder answers ``None``.** A record kind this release retired; the next
  write drops it.
- **Otherwise** the record loads.

Reads are a frozen snapshot keyed by the file's stat signature, so an unchanged
file costs one ``stat`` and no read, parse or validation. A reload decodes only
the lines whose bytes changed, because each record's encoded line is kept beside
it. The same line cache means a write re-encodes only the records that moved,
and a mutation that changes nothing writes nothing.
"""

from __future__ import annotations

import json
import os
import threading
import zlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from loguru import logger

from grove.core import paths
from grove.core.errors import GroveError

_FORMAT = "grove-records"
_PREFIX = '{"c":"'
_INFIX = '","r":'
# The payload starts at a fixed offset, so verifying a line slices it rather
# than re-encoding the parsed record.
_PAYLOAD_AT = len(_PREFIX) + 8 + len(_INFIX)


class RecordFileUnreadable(GroveError):
    """Nothing in the file can be trusted, and the store's policy is to refuse."""


@dataclass(frozen=True, slots=True)
class FileSignature:
    """Identity plus contents-relevant metadata of one published generation.

    An atomic replacement changes the inode and an in-place edit changes the
    mtime or size. Either way the snapshot describes a file that is gone.
    """

    device: int
    inode: int
    modified_ns: int
    size: int

    @classmethod
    def of(cls, path: Path) -> FileSignature | None:
        try:
            return cls.from_stat(path.stat())
        except FileNotFoundError:
            return None

    @classmethod
    def from_stat(cls, stat: os.stat_result) -> FileSignature:
        return cls(stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)


@dataclass(frozen=True, slots=True)
class RecordCodec[T]:
    """How one store's records cross the line boundary.

    ``decode`` raising ``ValueError`` (Pydantic's ``ValidationError`` is one),
    ``TypeError`` or ``KeyError`` marks a FOREIGN record that this build cannot
    read, never a corrupt one; corruption is the CRC's call. ``decode``
    returning ``None`` marks a record kind this release retired.
    """

    key: Callable[[T], str]
    encode: Callable[[T], dict[str, Any]]
    decode: Callable[[dict[str, Any]], T | None]


@dataclass(frozen=True, slots=True)
class Snapshot[T]:
    """One parsed generation of the file. Immutable, so it is shared, never copied."""

    signature: FileSignature | None
    records: Mapping[str, T]
    #: The exact line each record was read from or last written as.
    lines: Mapping[str, str]
    #: Lines another build wrote, kept verbatim and written back.
    foreign: tuple[str, ...] = ()


_EMPTY: Mapping[str, Any] = MappingProxyType({})

#: What a reader found that only a writer holding the file lock may fix.
_Repair = Literal["migrate", "corrupt", "unreadable"]


@dataclass(slots=True)
class _Read[T]:
    snapshot: Snapshot[T]
    repair: _Repair | None = None
    corrupt: list[str] = field(default_factory=list)
    reason: str = ""


OnUnreadable = Literal["raise", "quarantine"]


class RecordFile[T]:
    """A keyed set of records in one checksummed JSONL file, read through a snapshot.

    Several processes write the same file (the daemon, a ``grove`` CLI verb, the
    TUI), so every write holds :func:`paths.exclusive_lock` and publishes
    through :func:`paths.write_atomic`. Reads take no file lock, because a
    rename shows a reader one whole generation. A reader that finds something to
    repair (a previous format, a corrupt line, an unreadable file) takes the
    lock, re-reads, and repairs once, so two processes cannot repair at once.

    ``on_unreadable`` is the store's answer to "this file has no trustworthy
    header". ``"quarantine"`` moves the file aside and starts empty, right for a
    store whose contents can be rebuilt or lost safely. ``"raise"`` stays loud,
    right where an empty read would destroy something, like every paired
    device's session. A header naming a DIFFERENT version is never quarantined
    under either policy: it is another Grove's file, not a damaged one.
    """

    def __init__(
        self,
        path: Path,
        *,
        store: str,
        version: int,
        codec: RecordCodec[T],
        legacy: Callable[[bytes], list[dict[str, Any]]] | None = None,
        on_unreadable: OnUnreadable = "raise",
        mode: int = 0o600,
    ) -> None:
        self._path = path
        self._store = store
        self._version = version
        self._codec = codec
        self._legacy = legacy
        self._on_unreadable = on_unreadable
        self._mode = mode
        self._header = _dumps({"format": _FORMAT, "store": store, "version": version}) + "\n"
        # Guards the snapshot reference; the file lock orders writers.
        self._lock = threading.RLock()
        self._snapshot: Snapshot[T] | None = None
        #: Record lines decoded so far: the one cost the snapshot exists to avoid.
        self.decodes = 0

    @property
    def path(self) -> Path:
        return self._path

    # ─── reads ──────────────────────────────────────────────────────────────

    def snapshot(self) -> Snapshot[T]:
        """The current generation. One ``stat`` when the file has not changed."""
        with self._lock:
            signature = FileSignature.of(self._path)
            current = self._snapshot
            if current is not None and current.signature == signature:
                return current
            read = self._read(signature, current)
            if read.repair is not None:
                with paths.exclusive_lock(self._path):
                    self._snapshot = self._settled(current)
                return self._snapshot
            self._snapshot = read.snapshot
            return read.snapshot

    def records(self) -> Mapping[str, T]:
        return self.snapshot().records

    def get(self, key: str) -> T | None:
        return self.snapshot().records.get(key)

    # ─── writes ─────────────────────────────────────────────────────────────

    def mutate(self, change: Callable[[Mapping[str, T]], Mapping[str, T] | None]) -> Snapshot[T]:
        """Apply ``change`` to the latest records under the file lock and persist.

        ``change`` receives the current records and returns the complete new
        mapping, or ``None`` for nothing to do. A mapping equal to the current
        one writes nothing, so no caller has to skip a no-op itself. Only
        records whose value changed are re-encoded.
        """
        with self._lock, paths.exclusive_lock(self._path):
            current = self._settled(self._snapshot)
            self._snapshot = current
            proposed = change(current.records)
            if proposed is None:
                return current
            lines: dict[str, str] = {}
            moved = proposed.keys() != current.records.keys()
            for key, record in proposed.items():
                before = current.records.get(key)
                if before is not None and (before is record or before == record):
                    lines[key] = current.lines[key]
                else:
                    lines[key] = self._line(self._codec.encode(record))
                    moved = True
            if not moved:
                return current
            self._snapshot = self._publish(dict(proposed), lines, current.foreign)
            return self._snapshot

    def invalidate(self) -> None:
        """Forget the snapshot, for a caller that observed a write by other means."""
        with self._lock:
            self._snapshot = None

    # ─── reading the file ───────────────────────────────────────────────────

    def _read(self, signature: FileSignature | None, previous: Snapshot[T] | None) -> _Read[T]:
        """Parse the file as it is. Never writes; names any repair a writer must make."""
        if signature is None:
            return _Read(_empty())
        try:
            with self._path.open("rb") as fh:
                raw = fh.read()
                # An atomic replacement after open is a different inode than this
                # descriptor's, so bind the bytes to the descriptor they came from.
                signature = FileSignature.from_stat(os.fstat(fh.fileno()))
        except FileNotFoundError:
            return _Read(_empty())
        except OSError as exc:
            return _Read(_empty(), repair="unreadable", reason=f"cannot be read ({exc})")
        head, _, body = raw.partition(b"\n")
        refusal = self._header_problem(head, raw)
        if refusal is not None:
            return refusal
        return self._parse(signature, body, previous)

    def _header_problem(self, head: bytes, raw: bytes) -> _Read[T] | None:
        """What the header says about the whole file, or ``None`` when it is ours."""
        header = _json_object(head)
        if header is None or header.get("format") != _FORMAT or header.get("store") != self._store:
            if self._legacy is not None and _json_object(raw) is not None:
                return _Read(_empty(), repair="migrate")
            return _Read(_empty(), repair="unreadable", reason="has no readable records header")
        if header.get("version") != self._version:
            raise RecordFileUnreadable(
                f"{self._store} store at {self._path} has version "
                f"{header.get('version')!r} (this Grove reads {self._version}); "
                "refusing to treat it as empty"
            )
        return None

    def _parse(
        self, signature: FileSignature, body: bytes, previous: Snapshot[T] | None
    ) -> _Read[T]:
        known = {line: key for key, line in previous.lines.items()} if previous else {}
        records: dict[str, T] = {}
        lines: dict[str, str] = {}
        foreign: list[str] = []
        corrupt: list[str] = []
        for raw in body.split(b"\n"):
            if not raw:
                continue
            try:
                line = raw.decode("utf-8") + "\n"
            except UnicodeDecodeError:
                corrupt.append(raw.decode("utf-8", "replace"))
                continue
            reused = known.get(line)
            if reused is not None and previous is not None:
                records[reused] = previous.records[reused]
                lines[reused] = line
                continue
            payload = _verified_payload(line)
            data = _json_object(payload.encode("utf-8")) if payload is not None else None
            if data is None:
                corrupt.append(line.rstrip("\n"))
                continue
            self._classify(data, line, records, lines, foreign)
        snapshot = Snapshot(
            signature=signature,
            records=MappingProxyType(records),
            lines=MappingProxyType(lines),
            foreign=tuple(foreign),
        )
        self._warn_foreign(snapshot, previous)
        return _Read(snapshot, repair="corrupt" if corrupt else None, corrupt=corrupt)

    def _classify(
        self,
        data: dict[str, Any],
        line: str,
        records: dict[str, T],
        lines: dict[str, str],
        foreign: list[str],
    ) -> None:
        self.decodes += 1
        try:
            record = self._codec.decode(data)
        except (ValueError, TypeError, KeyError):
            foreign.append(line)
            return
        if record is None:
            return
        key = self._codec.key(record)
        records[key] = record
        lines[key] = line

    def _warn_foreign(self, snapshot: Snapshot[T], previous: Snapshot[T] | None) -> None:
        if snapshot.foreign and (
            previous is None or set(previous.foreign) != set(snapshot.foreign)
        ):
            logger.warning(
                "{} store {} holds {} record(s) this Grove cannot read; kept, not used",
                self._store,
                self._path,
                len(snapshot.foreign),
            )

    # ─── repairs (file lock held) ───────────────────────────────────────────

    def _settled(self, previous: Snapshot[T] | None) -> Snapshot[T]:
        """The current generation with any repair applied. Caller holds the file lock."""
        signature = FileSignature.of(self._path)
        if previous is not None and previous.signature == signature:
            return previous
        read = self._read(signature, previous)
        if read.repair == "migrate":
            return self._migrate()
        if read.repair == "corrupt":
            self._quarantine_lines(read.corrupt)
            return self._publish(
                dict(read.snapshot.records), dict(read.snapshot.lines), read.snapshot.foreign
            )
        if read.repair == "unreadable":
            return self._quarantine_file(read.reason)
        return read.snapshot

    def _migrate(self) -> Snapshot[T]:
        """Rewrite the store's previous single-document format as records, in place.

        The original bytes are kept beside the file as ``<file>.v1.bak`` before
        anything is rewritten, because this runs once per host and a mistake here
        is the one a later version cannot repair. A previous-format file the
        store's reader refuses (another version) stays refused, exactly as the old
        reader refused it; the file is never rewritten in that case.
        """
        assert self._legacy is not None
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return _empty()
        try:
            rows = self._legacy(raw)
        except (GroveError, ValueError) as exc:
            raise RecordFileUnreadable(
                f"{self._store} store at {self._path} could not be read in its previous "
                f"format: {exc}"
            ) from exc
        backup = self._path.with_name(self._path.name + ".v1.bak")
        if not backup.exists():
            paths.write_atomic(backup, raw.decode("utf-8"), mode=self._mode)
        records: dict[str, T] = {}
        lines: dict[str, str] = {}
        foreign: list[str] = []
        for data in rows:
            self._classify(data, self._line(data), records, lines, foreign)
        snapshot = self._publish(records, lines, tuple(foreign))
        logger.info(
            "{} store migrated to checksummed records: {} record(s), {} kept for a newer "
            "Grove; the previous file is {}",
            self._store,
            len(records),
            len(foreign),
            backup,
        )
        return snapshot

    def _quarantine_file(self, reason: str) -> Snapshot[T]:
        if self._on_unreadable == "raise":
            raise RecordFileUnreadable(f"{self._store} store at {self._path} {reason}")
        aside = self._path.with_name(f"{self._path.name}.unreadable-{_stamp()}")
        try:
            self._path.replace(aside)
        except OSError as exc:
            raise RecordFileUnreadable(
                f"{self._store} store at {self._path} {reason}, and could not be moved aside: {exc}"
            ) from exc
        logger.error(
            "{} store at {} {}; moved it aside to {} and started empty",
            self._store,
            self._path,
            reason,
            aside,
        )
        return _empty()

    def _quarantine_lines(self, lines: list[str]) -> None:
        aside = self._path.with_name(self._path.name + ".corrupt")
        stamp = _stamp()
        fd = os.open(aside, os.O_WRONLY | os.O_CREAT | os.O_APPEND, self._mode)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.writelines(f"{stamp} {line}\n" for line in lines)
        logger.error(
            "{} store at {}: {} record line(s) failed their checksum and were dropped; "
            "the original bytes are in {}",
            self._store,
            self._path,
            len(lines),
            aside,
        )

    # ─── writing the file ───────────────────────────────────────────────────

    def _line(self, data: dict[str, Any]) -> str:
        payload = _dumps(data)
        return f"{_PREFIX}{_crc(payload)}{_INFIX}{payload}}}\n"

    def _publish(
        self, records: dict[str, T], lines: dict[str, str], foreign: tuple[str, ...]
    ) -> Snapshot[T]:
        body = "".join(foreign) + "".join(lines.values())
        paths.write_atomic(self._path, self._header + body, mode=self._mode)
        return Snapshot(
            signature=FileSignature.of(self._path),
            records=MappingProxyType(records),
            lines=MappingProxyType(lines),
            foreign=foreign,
        )


def _verified_payload(line: str) -> str | None:
    """The record JSON inside ``line`` if its CRC matches, else ``None``."""
    if not line.startswith(_PREFIX) or not line.endswith("}\n"):
        return None
    if line[len(_PREFIX) + 8 : _PAYLOAD_AT] != _INFIX:
        return None
    payload = line[_PAYLOAD_AT:-2]
    return payload if _crc(payload) == line[len(_PREFIX) : len(_PREFIX) + 8] else None


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _crc(payload: str) -> str:
    return f"{zlib.crc32(payload.encode('utf-8')):08x}"


def _empty() -> Snapshot[Any]:
    return Snapshot(signature=None, records=_EMPTY, lines=_EMPTY)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


__all__ = [
    "FileSignature",
    "RecordCodec",
    "RecordFile",
    "RecordFileUnreadable",
    "Snapshot",
]
