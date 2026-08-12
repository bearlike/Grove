"""Durable parse-derived facts for host-wide session rows.

One question: *what does a full transcript parse say about each session on this
host, without re-reading a gigabyte of transcripts every time someone lists
them?* Two facts answer to it today — the turn count and the two duration
clocks — and both come from the adapter that understands the format
(``parse_activity(...).human_turns`` and ``duration_of(read_messages(...))``,
the same numbers the project-scoped listing computes for itself). That costs a
full parse (measured: 40.3 s for 518 sessions / 1.24 GB on the reference host).
Every cheaper source was measured and refused; the verdicts live in
[contracts](contracts/CLAUDE.md). So the cost is not avoided, it is **paid
once per session VERSION**: a durable ``(mtime, size)``-keyed file means an
unchanged transcript is never read twice, and the parsing pass runs off the
request path entirely (:meth:`TurnCountCache.fill`).

**One cache, one fingerprint, one background pass, however many facts.** A
second durable file per column would re-walk the same transcripts on its own
schedule and could disagree with this one about what "current" means; both
facts fall out of the SAME parse, so they are stored and invalidated together.
Adding a third copies that shape — extend :class:`SessionFacts`, never add a
sibling cache. The cost of storing them together is that a widened
:class:`SessionFacts` makes every previously-written entry undecodable and
therefore unanswered, so the next pass re-parses the host once; that is the
usage cache's "a schema change rebuilds" rule, and it is why there is no
migration code here.

Nothing here is on the read path's critical section. A missing, truncated or
foreign cache file degrades to "nothing is known" — every row reports ``None``,
which the wire already contracts as *not measured at this scope*, never ``0``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Final

from loguru import logger

from grove.core import paths
from grove.core.agents import SessionRef, get_adapter
from grove.core.contracts.usage import DurationView
from grove.core.session_duration import duration_of

_SAVE_EVERY = 25
"""Sessions measured between durable saves.

The point is not write amplification (the file is ~50 KB) but that a cold pass
takes tens of seconds and a browsing user should watch rows FILL IN rather than
stare at a full column of em dashes until the last session is parsed. Small
enough that the wait is seconds, large enough that the pass is not 500 atomic
rewrites.
"""

# Session identity as the catalog deals in it: the same ``(kind, session_id)``
# pair `SessionCatalog.scan` dedupes on, so a cache hit cannot bind to the wrong
# row. The on-disk key joins the two because JSON object keys are strings.
CountKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class SessionFacts:
    """What one full parse of a session's transcripts yields.

    Every member is a fact only that parse can produce — anything a bounded head
    read or a ``stat`` already answers belongs on ``SessionRef``, not here, and
    caching it would buy a staleness bug for nothing.

    ``duration`` is always present on a measured session, and its own fields
    carry the "nothing measurable" case (a spine with no timestamped work reads
    as an all-``None`` view with ``confidence="unknown"``). So the *row's*
    ``None`` keeps meaning exactly one thing: not measured yet.
    """

    turns: int
    duration: DurationView


@dataclass(frozen=True, slots=True)
class _Remembered:
    """One session's facts plus the transcript fingerprint they were taken at."""

    mtime: float
    size: int
    facts: SessionFacts


def _entry_shape() -> str:
    """A fingerprint of what an entry STORES, so widening it invalidates the file.

    **The fingerprint on an entry answers "has the transcript changed"; this
    answers "has the QUESTION changed", and the cache needs both.** A finished
    session's transcript never changes again, so its `(mtime, size)` matches
    forever — which is the property that makes this cache cheap, and also the
    property that makes a widened `SessionFacts` permanently wrong. Every
    pre-existing row would keep answering, with the new members absent, and an
    absent member reads as *not measured* — indistinguishable from a session
    that genuinely had nothing to measure, for the life of the file.

    `_decode` alone cannot catch it. It refuses an entry missing the `duration`
    KEY, but `DurationView.model_validate` happily accepts a dict missing
    newly-added nullable FIELDS and fills them with `None`. That is exactly
    what happened when `generation_ms`/`tool_ms` landed: the guard held for
    widening `SessionFacts` and failed for widening a model nested inside it.
    Verified by decoding a real pre-change payload — it came back clean, with
    both new fields `None`.

    Derived from the field sets rather than hand-numbered, because a constant
    somebody must remember to bump is the same trap one level up: this cannot
    go stale without the shape it describes going stale with it.
    """
    return "|".join(
        (
            ",".join(sorted(field.name for field in fields(SessionFacts))),
            ",".join(sorted(DurationView.model_fields)),
        )
    )


_ENTRY_SHAPE: Final = _entry_shape()


def _key(ref: SessionRef) -> CountKey:
    return (ref.adapter_kind, ref.session_id)


def _fingerprint(path: Path) -> tuple[float, int] | None:
    """``(mtime, size)`` for ``path``, or ``None`` when it cannot be stat'd.

    The cheap change detector, not a content hash — the same choice ``cclens``
    and the usage cache's ``ingested_files`` already make. ``mtime`` alone would
    very nearly do (transcripts are append-only), and ``size`` costs nothing
    beside it while catching a replacement that preserved a timestamp.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


class TurnCountCache:
    """The parse-fact file on disk — best-effort in both directions.

    Two methods, one for each side of the seam: :meth:`facts_for` is the READ
    (one file read, one ``stat`` per remembered row, zero parses — 1.6 ms for
    518 sessions), :meth:`fill` is the WRITE (a full adapter parse per session
    whose transcript has changed or was never seen). A caller that must answer a
    request calls only the first.

    Nothing raises. An unreadable, truncated or foreign file degrades to no
    facts at all, which is exactly the state that shipped before it existed.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.session_turns_path()

    def facts_for(self, refs: Iterable[SessionRef]) -> Mapping[CountKey, SessionFacts]:
        """The parse facts for every ref this cache can already answer.

        A ref is answered only when its transcript still carries the exact
        fingerprint the facts were taken at, so a session that has grown by one
        turn is simply absent — never answered with the stale numbers. Absent
        means the row renders ``None``, which is the whole reason those fields
        are nullable.
        """
        remembered = self._load()
        if not remembered:
            return {}
        out: dict[CountKey, SessionFacts] = {}
        for ref in refs:
            entry = remembered.get(_key(ref))
            if entry is None or ref.transcript_path is None:
                continue
            if _fingerprint(ref.transcript_path) == (entry.mtime, entry.size):
                out[_key(ref)] = entry.facts
        return out

    def fill(self, refs: Sequence[SessionRef], *, stop: Callable[[], bool] | None = None) -> int:
        """Measure every ref this cache cannot answer, persisting as it goes.

        Blocking and unbounded by contract — a cold pass over a whole host is
        tens of seconds — so it belongs on a background worker, never inside a
        request. ``stop`` is polled between sessions so a shutdown does not wait
        out the pass; whatever was measured before it fired is already durable.

        Takes the WHOLE ref set rather than the misses alone, because the set is
        also what prunes: a session whose transcript is gone is gone from the
        file too. The prune is scoped to what the caller saw, and the caller is
        the host-wide catalog; a narrower caller would at worst force a
        re-measure, never produce a wrong number.
        """
        keep = {_key(ref) for ref in refs}
        answered = self.facts_for(refs)
        measured: dict[CountKey, _Remembered] = {}
        total = 0
        for ref in refs:
            if stop is not None and stop():
                break
            key = _key(ref)
            if key in answered or ref.cwd is None or ref.transcript_path is None:
                continue
            # The fingerprint is captured BEFORE the read, so a writer appending
            # mid-parse leaves a mismatch the next pass re-reads, rather than
            # blessing bytes this measurement never saw (the usage cache's rule).
            before = _fingerprint(ref.transcript_path)
            if before is None:
                continue
            facts = self._measure(ref)
            if facts is None:
                continue
            measured[key] = _Remembered(mtime=before[0], size=before[1], facts=facts)
            total += 1
            if len(measured) >= _SAVE_EVERY:
                self._merge(measured, keep=keep)
                measured = {}
        self._merge(measured, keep=keep)
        return total

    @staticmethod
    def _measure(ref: SessionRef) -> SessionFacts | None:
        """One session's parse facts, or ``None`` when nothing can read it.

        The adapter's own projections, never a second parser: these are the
        identical computations the project-scoped listing publishes in these
        fields, so the two scopes cannot report different numbers for one
        session. Both calls resolve the same transcript set, so the second rides
        the first's fold and pays only its own projection. ``cwd`` is the
        coordinate the adapter resolves a session under, which is why a row
        without one is permanently unmeasurable rather than measured as zero.
        """
        try:
            adapter = get_adapter(ref.adapter_kind)
            cwd = Path(str(ref.cwd))
            return SessionFacts(
                turns=adapter.parse_activity(cwd, ref.session_id).human_turns,
                duration=duration_of(adapter.read_messages(cwd, ref.session_id)),
            )
        except Exception as exc:  # a browse column must never break a scan
            logger.debug(
                "session facts unavailable for {} {}: {}",
                ref.adapter_kind,
                ref.session_id,
                type(exc).__name__,
            )
            return None

    def _load(self) -> dict[CountKey, _Remembered]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            decoded = json.loads(raw)
        except ValueError:
            logger.debug("session fact cache is not valid JSON; measuring from scratch")
            return {}
        if not isinstance(decoded, dict):
            return {}
        # Written by a build that stored a different set of facts — see
        # `_entry_shape`. Discard wholesale rather than adopt entries whose
        # missing members would read as "not measured" forever.
        if decoded.get("shape") != _ENTRY_SHAPE:
            logger.debug("session fact cache was written for a different shape; re-measuring")
            return {}
        sessions = decoded.get("sessions")
        if not isinstance(sessions, dict):
            return {}
        out: dict[CountKey, _Remembered] = {}
        for key, payload in sessions.items():
            kind, sep, session_id = str(key).partition("\t")
            if not sep or not isinstance(payload, dict):
                continue
            entry = _decode(payload)
            if entry is not None:
                out[(kind, session_id)] = entry
        return out

    def _merge(self, measured: Mapping[CountKey, _Remembered], *, keep: set[CountKey]) -> None:
        """Publish ``measured``, keeping every still-live entry and dropping the rest.

        Read-modify-write under the same exclusive lock the workspace store
        uses, for the reason that store documents: publication is a rename, so
        two writers otherwise clobber each other's facts wholesale. The write
        is skipped when nothing would change, so a fully-measured host pays one
        read per pass and no writes at all.
        """
        try:
            with paths.exclusive_lock(self._path):
                current = self._load()
                merged = {k: v for k, v in current.items() if k in keep}
                if not measured and merged == current:
                    return
                merged.update(measured)
                paths.write_atomic(
                    self._path,
                    json.dumps(
                        {
                            "shape": _ENTRY_SHAPE,
                            "sessions": {
                                f"{kind}\t{session_id}": _encode(entry)
                                for (kind, session_id), entry in sorted(merged.items())
                            },
                        }
                    ),
                )
        except OSError as exc:
            logger.debug("could not persist session facts: {}", type(exc).__name__)


def _encode(entry: _Remembered) -> dict[str, Any]:
    return {
        "mtime": entry.mtime,
        "size": entry.size,
        "turns": entry.facts.turns,
        "duration": entry.facts.duration.model_dump(),
    }


def _decode(payload: Mapping[str, Any]) -> _Remembered | None:
    """One on-disk entry, or ``None`` for anything this build cannot read whole.

    A partial entry is refused rather than half-adopted: a row whose duration
    was written by an older build (no ``duration`` key at all) must read as
    *not measured* so the next pass re-parses it, exactly like a fingerprint
    mismatch. Nothing here migrates — the file is derived from transcripts
    Grove can re-read, so rebuilding is always cheaper than being wrong.
    """
    try:
        duration = DurationView.model_validate(payload["duration"])
        return _Remembered(
            mtime=float(payload["mtime"]),
            size=int(payload["size"]),
            facts=SessionFacts(turns=int(payload["turns"]), duration=duration),
        )
    except (KeyError, TypeError, ValueError):
        return None


__all__ = ["CountKey", "SessionFacts", "TurnCountCache"]
