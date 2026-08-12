"""When may this account be probed, and what does Grove show until then.

Two classes, split along the side-effect line the root file draws.
:class:`QuotaProbeState` is the whole decision as data — pure, clock-injected,
and testable without a socket or a sleep. :class:`QuotaStateFile` is the edge
that makes one instance of it outlive the process that produced it.

**The durability is the point, not an optimization.** A last-good snapshot held
only in process memory answers the question "what did we know" for exactly as
long as nobody restarts the daemon — and a Grove update restarts it, which is
the one moment an operator is most likely to be looking. Measured on this host:
a healthy three-window reading collected at 10:33 was gone by 12:47 with
``observed_at: null`` and ``windows: []``, because the daemon was restarted
twice in between and the failing read that followed had no memory to fall back
to. The file also makes the fleet's several processes — daemon, TUI, `grove`
CLI — share one probe budget instead of each holding a private cache and each
paying its own request to learn the same number.

Nothing here is a credential. The ledger holds account ids, operator labels,
percentages, window durations and timestamps: the same facts the wire already
carries, which is precisely why it can be written to disk at all.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict

from grove.core import paths
from grove.core.contracts.usage import BillingAccountView, QuotaStatus
from grove.core.usage.quota.base import observed_age_seconds

_FAILURE_BACKOFF_STATUSES: frozenset[QuotaStatus] = frozenset({"auth_expired", "rate_limited"})
"""Outcomes that earn an account a growing cool-off.

Both mean *nothing will change until something outside Grove changes* — a human
signs in, or a window elapses — so asking again on the ordinary cadence can only
turn a soft refusal into a hard one. ``unreachable`` is deliberately absent: it
clears on its own, and the TTL already bounds how often it is retried."""


class QuotaProbeState(BaseModel):
    """One account's last answer, its last answer WITH DATA, and its cool-off.

    ``last_good`` is separate from ``last_view`` because that separation IS the
    stale-serving feature: the moment a refresh fails, the previous reading is
    the most useful thing on the page, and it is only still there because the
    failure was never allowed to overwrite it.

    **"Good" means the reading carried windows, not that its status was ``ok``.**
    A Codex account whose recorded window has rolled over reports ``stale`` and
    still carries the newest numbers Grove has; keying on the status word would
    file that newer reading as a failure and answer with the older one, which is
    the exact inversion this class exists to prevent.

    Every method is pure and takes ``now``. The class is Pydantic rather than a
    dataclass because instances are serialized to a file that outlives the
    process and is re-read by a different one — a format, and formats validate.
    """

    model_config = ConfigDict(extra="ignore")

    last_view: BillingAccountView | None = None
    """The raw answer of the most recent collection, good or bad."""

    last_good: BillingAccountView | None = None
    """The most recent answer that actually carried windows."""

    fetched_at: datetime | None = None
    """When the provider was last CONTACTED — the TTL's anchor. ``None`` means
    this account has never been collected, which is the one state that always
    permits a probe."""

    retry_not_before: datetime | None = None
    consecutive_failures: int = 0

    probe_count: int = 0
    """How many upstream reads this account has cost, across every process.

    The durable answer to "how often is Grove really contacting this provider",
    and the only one on an ordinary install: the per-probe log line is emitted
    below the default sink's level, so a healthy probe leaves no trace anywhere
    and the rate can never be reconstructed after the fact — which is exactly
    the position the rate limit was first diagnosed from. Two processes probing
    in the same instant can lose an increment to the ledger merge, so this reads
    as a floor; the TTL already makes that overlap rare, and undercounting is
    the safe direction for a number an operator uses to decide whether Grove is
    the one knocking.
    """

    # ─── the pure decision ──────────────────────────────────────────────────

    def may_probe(self, now: datetime, *, ttl_seconds: int, force: bool) -> bool:
        """Is Grove allowed to contact the provider for this account right now?

        The cool-off outranks ``force`` deliberately: a floor a caller can
        bypass is not a floor, and the explicit-refresh button is exactly what a
        frustrated operator presses repeatedly at an account that is already
        rate-limited — the one situation in which extra requests make it worse.
        """
        if self.fetched_at is None:
            return True
        if self.retry_not_before is not None and now < self.retry_not_before:
            return False
        if force:
            return True
        return now - self.fetched_at >= timedelta(seconds=ttl_seconds)

    def record(
        self,
        view: BillingAccountView,
        now: datetime,
        *,
        floor_seconds: int,
        max_seconds: int,
        retry_after_seconds: float | None = None,
    ) -> QuotaProbeState:
        """The state after one collection. Returns a new instance; mutates none.

        A reading with windows resets the failure count, because the account is
        demonstrably answering again. A failing one grows the cool-off, and the
        provider's own ``Retry-After`` wins whenever it asks for longer than
        Grove's schedule would have waited — arguing with a rate limiter about
        when to come back is how a soft limit becomes a hard one.
        """
        carries_data = self.carries_reading(view)
        cooling = not carries_data and self._earns_cool_off(view.status, retry_after_seconds)
        failures = self.consecutive_failures + 1 if cooling else 0
        wait = (
            self._cool_off(failures, floor_seconds, max_seconds, retry_after_seconds)
            if cooling
            else None
        )
        return QuotaProbeState(
            last_view=view,
            last_good=view if carries_data else self.last_good,
            fetched_at=now,
            retry_not_before=None if wait is None else now + timedelta(seconds=wait),
            consecutive_failures=failures,
            probe_count=self.probe_count + 1,
        )

    @staticmethod
    def carries_reading(view: BillingAccountView) -> bool:
        """Did this answer actually measure anything?

        The discriminator for "worth keeping as last-known-good", and it is
        deliberately about the PAYLOAD rather than the status word. A Codex
        account whose recorded window has rolled over reports ``stale`` and still
        carries the newest numbers Grove has; filing that as a failure would
        answer with the older reading, which is this class's own bug inverted.
        ``spend`` counts for the same reason — it is what an API-key account
        measures instead of windows, and a rule naming only windows would quietly
        never remember one.
        """
        return bool(view.windows) or view.spend is not None

    def render(self, now: datetime) -> BillingAccountView | None:
        """What a caller sees: the newest data Grove has, plus why it is not newer.

        ``None`` only when this account has never been collected at all, which is
        the caller's signal to fall back to the provider's local description.
        """
        if self.last_view is None:
            return None
        if self.carries_reading(self.last_view) or self.last_good is None:
            # Either the latest read carried data, or there is no older reading
            # to fall back to — in both cases the latest read IS the answer, and
            # the only thing to add is when Grove will try again.
            return self.last_view.model_copy(
                update={
                    "last_error": None if self.last_view.windows else self.last_view.status,
                    "retry_after": self.retry_not_before,
                }
            )
        return self.last_good.model_copy(
            update={
                "status": "stale",
                "detail": self.last_view.detail,
                "last_error": self.last_view.status,
                "stale_seconds": observed_age_seconds(self.last_good.observed_at, now),
                "retry_after": self.retry_not_before,
            }
        )

    def relabelled(self, label: str) -> QuotaProbeState:
        """This state with an operator's current label applied to both readings.

        The label is the one field of a snapshot that is config rather than
        evidence, so an operator adding ``usage.quota.labels`` must see it
        without waiting out a TTL — or a restart, now that the readings persist.
        """
        if all(v is None or v.label == label for v in (self.last_view, self.last_good)):
            return self
        return self.model_copy(
            update={
                "last_view": _relabel(self.last_view, label),
                "last_good": _relabel(self.last_good, label),
            }
        )

    # ─── cool-off arithmetic ────────────────────────────────────────────────

    @staticmethod
    def _earns_cool_off(status: QuotaStatus, retry_after_seconds: float | None) -> bool:
        """A provider that asked to be left alone is obeyed whatever it returned."""
        return status in _FAILURE_BACKOFF_STATUSES or retry_after_seconds is not None

    @staticmethod
    def _cool_off(
        failures: int,
        floor_seconds: int,
        max_seconds: int,
        retry_after_seconds: float | None,
    ) -> float:
        """Seconds to wait: the provider's instruction, or a doubling schedule.

        The schedule doubles from ``floor_seconds`` because a fixed floor against
        a limit that has not lifted is still a request every floor, forever —
        which is what a rate limiter reads as continuing to knock. A provider
        that asked for longer wins; one that asked for less does not, because the
        header bounds when the LIMIT lifts, not how often Grove should ask.
        """
        scheduled = min(float(max_seconds), floor_seconds * (2.0 ** max(0, failures - 1)))
        if retry_after_seconds is None:
            return scheduled
        return max(scheduled, min(float(max_seconds), retry_after_seconds))


def _relabel(view: BillingAccountView | None, label: str) -> BillingAccountView | None:
    return None if view is None or view.label == label else view.model_copy(update={"label": label})


class QuotaStateFile:
    """The probe ledger on disk — best-effort in both directions.

    Under the STATE dir beside the workspace state, and deliberately NOT in
    ``usage.sqlite3``: that file is derived wholly from transcripts and its
    licence is that deleting it is always safe, whereas a last-known-good
    reading is the one fact in this subsystem nothing can reconstruct. It is
    also why a schema bump there rebuilds the whole cache, which would be an
    absurd price for a quota row.

    Nothing here raises. An unreadable, truncated or foreign ledger degrades to
    "no history", which is exactly the state Grove was in before the file
    existed; a failed write costs the next process its head start and nothing
    else. A quota page must never be the thing that fails a daemon start.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.quota_state_path()

    def load(self) -> dict[str, QuotaProbeState]:
        """Every persisted account state, keyed by ``account_id``."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            decoded = json.loads(raw)
        except ValueError:
            logger.debug("quota state ledger is not valid JSON; starting with no history")
            return {}
        if not isinstance(decoded, dict):
            return {}
        states: dict[str, QuotaProbeState] = {}
        for account_id, payload in decoded.get("accounts", {}).items():
            try:
                states[str(account_id)] = QuotaProbeState.model_validate(payload)
            except ValueError:
                logger.debug("dropping unreadable quota state for {}", account_id)
        return states

    def merge(self, updates: Mapping[str, QuotaProbeState]) -> None:
        """Persist ``updates``, keeping every account this process did not touch.

        A merge rather than a replace because the ledger is shared across
        processes whose configured selections legitimately differ — a `grove`
        CLI run with a narrower ``usage.quota.profiles`` must not delete the
        daemon's history for the accounts it was not asked about. The whole
        read-modify-write is held under the same exclusive lock the workspace
        store uses, since two writers publishing by rename is otherwise
        last-writer-wins over each other's readings.
        """
        if not updates:
            return
        try:
            with paths.exclusive_lock(self._path):
                merged = self.load()
                merged.update(updates)
                paths.write_atomic(
                    self._path,
                    json.dumps(
                        {
                            "accounts": {
                                key: state.model_dump(mode="json", exclude_none=True)
                                for key, state in merged.items()
                            }
                        },
                        indent=2,
                    ),
                )
        except OSError as exc:
            logger.debug("could not persist quota state: {}", type(exc).__name__)


__all__ = ["QuotaProbeState", "QuotaStateFile"]
