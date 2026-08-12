"""Burn rate for one subscription window: is this pace under the limit at reset?

The whole point of this module is what it does NOT do. Reading a quota endpoint
is itself a metered act — it is the thing that rate-limited this host on
2026-08-10 and the reason the rest of this package exists — so a burn-rate
indicator that cost one extra request per render would be a feature that
consumes the budget it reports on. Everything here is arithmetic over a reading
Grove already has: a percentage, the window's own bounds, and an injected clock.
Zero network, zero provider reads, nothing persisted.

It is deliberately arithmetic and not a model. A linear extrapolation of "used
so far, over elapsed so far" is a sentence a reader can check against the two
numbers beside it; anything cleverer would be an unexplainable answer about a
budget the user cannot re-derive.

**The refusals carry more weight than the formula.** A quota reading is a
percentage the providers round to whole numbers, so early in a window the
extrapolation is dominated by that rounding rather than by anyone's pace — see
:attr:`WindowBurnRate.MIN_ELAPSED_PERCENT`. And a window whose duration no
provider reported has no computable start at all, which is the ordinary case for
one of the two shipped providers: such a window reports ``unknown`` and stays
there rather than borrowing a duration from folklore.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Literal

from grove.core.contracts.usage import SubscriptionWindowProjection

_LIMIT_PERCENT = 100.0
"""What "the limit" means. Not a threshold and not configurable: it is the
definition of a percentage-of-quota reading. The two judgement thresholds that
ARE opinions live in config instead."""


@dataclass(frozen=True, slots=True)
class WindowBurnRate:
    """Projects one window's usage forward to its own reset.

    Pure: the caller supplies the facts and the clock, so the whole decision is
    testable with a hand-built window and a fixed instant. The two verdict
    thresholds come from :class:`~grove.core.config.UsageQuotaConfig`, because
    "when is a week's spending worrying" is a judgement about somebody's
    workload rather than a fact about their quota.
    """

    clock: Callable[[], datetime]
    tight_percent: float = 85.0
    over_percent: float = 100.0

    MIN_ELAPSED_PERCENT: ClassVar[float] = 10.0
    """How much of a window must have elapsed before its pace is published.

    Both shipped providers report usage as a percentage rounded to a whole
    number (verified against live readings on 2026-08-11: 0, 3, 25 and 63 across
    two accounts). Dividing by an elapsed fraction magnifies that rounding step
    by ``100 / elapsed``, so at 10% elapsed one unreported point of usage is
    already worth ten points of projection, and below that a single rounding
    step walks the verdict across a threshold on its own. Ten percent of a
    weekly window is about seventeen hours and of a five-hour window about
    thirty minutes — early enough to be useful, late enough to mean anything.
    """

    def project(
        self,
        *,
        used_percent: float | None,
        starts_at: datetime | None,
        ends_at: datetime | None,
        tokens_used: int | None = None,
    ) -> SubscriptionWindowProjection:
        """This window's pace, or an honestly empty projection.

        ``starts_at``/``ends_at`` are the window's own bounds; a provider that
        reported no duration leaves the start unknown and gets ``unknown`` back.
        ``tokens_used`` is measured evidence the caller looked up for those same
        bounds, and rides through even where the pace itself is withheld — a
        measurement is not an extrapolation and does not share its gate.
        """
        available = self._available_estimate(used_percent, tokens_used)
        elapsed_percent = self._elapsed_percent(starts_at, ends_at)
        resolved = self._resolved_window_seconds(starts_at, ends_at)
        if (
            used_percent is None
            or elapsed_percent is None
            or elapsed_percent < self.MIN_ELAPSED_PERCENT
        ):
            return SubscriptionWindowProjection(
                elapsed_percent=elapsed_percent,
                resolved_window_seconds=resolved,
                tokens_used=tokens_used,
                tokens_available_estimate=available,
            )
        burn_rate = used_percent / elapsed_percent
        projected = burn_rate * _LIMIT_PERCENT
        return SubscriptionWindowProjection(
            elapsed_percent=elapsed_percent,
            resolved_window_seconds=resolved,
            burn_rate=burn_rate,
            projected_percent=projected,
            verdict=self._verdict(projected),
            exhausts_at=self._exhausts_at(
                used_percent, starts_at=starts_at, ends_at=ends_at, projected=projected
            ),
            tokens_used=tokens_used,
            tokens_available_estimate=available,
        )

    # ─── pure parts ─────────────────────────────────────────────────────────

    def _elapsed_percent(
        self, starts_at: datetime | None, ends_at: datetime | None
    ) -> float | None:
        """How far into the window ``now`` is, or ``None`` if that is unknowable.

        Capped at 100 rather than running past it: once a window has reached its
        reset, the reading on show describes a window that is over, and treating
        it as 140% elapsed would deflate its own projection.
        """
        elapsed = self._elapsed_seconds(starts_at, ends_at)
        if elapsed is None or starts_at is None or ends_at is None:
            return None
        return elapsed / (ends_at - starts_at).total_seconds() * _LIMIT_PERCENT

    def _elapsed_seconds(
        self, starts_at: datetime | None, ends_at: datetime | None
    ) -> float | None:
        """Seconds of the window spent so far, or ``None`` for an unusable pair.

        A zero-or-negative-length window and a clock reading from before the
        window opened are both refused here, which is what lets every division
        downstream skip its own guard.
        """
        if starts_at is None or ends_at is None:
            return None
        if (ends_at - starts_at).total_seconds() <= 0:
            return None
        elapsed = (min(self.clock(), ends_at) - starts_at).total_seconds()
        return elapsed if elapsed > 0 else None

    @staticmethod
    def _resolved_window_seconds(
        starts_at: datetime | None, ends_at: datetime | None
    ) -> int | None:
        """The span this projection was computed against, whoever supplied it.

        Derived from the bounds rather than taken as a separate argument, so it
        cannot disagree with the elapsed fraction and the verdict sitting beside
        it — the caller resolves provider-or-operator duration ONCE, into
        ``starts_at``, and everything downstream reads that single answer.
        """
        if starts_at is None or ends_at is None:
            return None
        seconds = (ends_at - starts_at).total_seconds()
        return round(seconds) if seconds > 0 else None

    def _verdict(self, projected: float) -> Literal["on_track", "tight", "over"]:
        if projected < self.tight_percent:
            return "on_track"
        return "tight" if projected <= self.over_percent else "over"

    def _exhausts_at(
        self,
        used_percent: float,
        *,
        starts_at: datetime | None,
        ends_at: datetime | None,
        projected: float,
    ) -> datetime | None:
        """When this pace reaches the limit — only if it does so before reset.

        A window with headroom returns ``None`` rather than a date beyond its own
        reset: a client renders this as "you run out then", and a moment the
        account never reaches is exactly the confidently wrong answer the rest of
        this package spends its code refusing.
        """
        elapsed = self._elapsed_seconds(starts_at, ends_at)
        if elapsed is None or starts_at is None or used_percent <= 0:
            return None
        if projected < _LIMIT_PERCENT:
            return None
        return starts_at + timedelta(seconds=_LIMIT_PERCENT / used_percent * elapsed)

    @staticmethod
    def _available_estimate(used_percent: float | None, tokens_used: int | None) -> int | None:
        """The window's whole allowance in tokens, extrapolated from one point.

        Both inputs are required and ``used_percent`` must be above zero: with no
        percentage there is nothing to scale by, and a window reporting nothing
        used says nothing about how large it is. Absent means absent — a token
        budget nobody has measured must never render as ``0``.
        """
        if tokens_used is None or used_percent is None or used_percent <= 0:
            return None
        return round(tokens_used / used_percent * _LIMIT_PERCENT)


__all__ = ["WindowBurnRate"]
