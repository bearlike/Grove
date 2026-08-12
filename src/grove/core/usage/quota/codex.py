"""Codex quota — the windows the CLI already wrote into its own rollouts.

Verified against **169 real on-host rollouts (50,988 ``rate_limits`` records)**
on **2026-08-09**, codex-cli 0.125.0 and older. This arm needs no credential and
no network: the CLI records the server's rate-limit block on its own
``event_msg``/``token_count`` rows, so the evidence for "what remains" is
already sitting in the store Grove reads for transcripts.

Four facts from that census shape the parser, and three of them are traps:

* **``primary`` is not the short window.** 8,774 records carry
  ``primary.window_minutes == 300`` and 8,621 carry
  ``secondary.window_minutes == 10080`` — but 33 records carry
  ``primary.window_minutes == 10080`` with ``secondary: null``. A parser that
  maps the KEY to a duration reports a weekly budget as a 5-hour one on exactly
  the accounts whose plan changed. The duration comes from the payload; the key
  is only a label.
* **The window is not always a round number.** 153 records report
  ``secondary.window_minutes == 10081``. Any code comparing against ``10080``
  to recognize "the weekly one" is already wrong on this host.
* **Most records carry the block and no window.** 42,181 of 50,988 have a null
  ``resets_at``, and only 8,807 carry any window at all — the CLI writes the
  envelope on every token count and fills it only when the server said
  something. Scanning must skip empty envelopes rather than stop at the first.
* **The PLAN rides the same block.** ``plan_type`` names the subscription
  (``plus``, ``team``, ``prolite`` on this host) beside the windows, and is
  ``null`` on most records — so it is read from the block the reported windows
  came from and never scanned for separately, since a plan and a percentage from
  two different months describe two different accounts.
* **Windows beyond the two named keys are anticipated but unobserved.**
  ``individual_limit`` appears (33 records, always null) alongside ``credits``,
  ``plan_type``, ``spend_control_reached`` and ``rate_limit_reached_type``. Any
  key whose value is a window-shaped object is parsed, so a third window costs
  nothing when it arrives.

**A rollout observation is a fact about the past, and its own ``resets_at``
says when it stopped being true.** Past the reset the window has rolled over
and the percentage describes a budget that no longer exists, so the account
reports ``stale`` with the observation's age. Before the reset the number is a
valid — if possibly low — reading of the live window, and reports ``ok``.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from grove.core.contracts.usage import (
    BillingAccountView,
    SubscriptionTier,
    SubscriptionWindowView,
    UsageProvider,
)
from grove.core.usage.quota.base import (
    QuotaAccount,
    QuotaProvider,
    as_float,
    observed_age_seconds,
    parse_epoch,
    parse_iso_datetime,
    subscription_tier,
)

_MARKER = '"rate_limits"'
"""Substring gate before a JSON parse. A rollout is mostly conversation and a
long one is megabytes; parsing every line to find one field is the difference
between a stat-cheap read and a page that stalls."""


@dataclass(frozen=True, slots=True)
class _RateLimitSnapshot:
    """One usable ``rate_limits`` block plus when the CLI recorded it."""

    observed_at: datetime | None
    payload: dict[str, Any]


class CodexQuotaProvider(QuotaProvider):
    """Rate-limit windows for one ``CODEX_HOME`` profile root, read locally."""

    provider: ClassVar[UsageProvider] = "codex"

    metered: ClassVar[bool] = False
    """Nothing here contacts a server, so nothing here can be refused.

    The whole answer is a bounded tail read of rollout files the CLI already
    wrote — measured at ~3 ms on a real profile — so holding it behind a cache
    window would only make it older in exchange for nothing.
    """

    MAX_ROLLOUTS: ClassVar[int] = 5
    """Newest rollout files searched before giving up.

    A profile whose five most recent sessions all predate the server sending any
    rate-limit data has nothing current to report anyway, and an unbounded walk
    of a date-partitioned tree is exactly the host-wide scan the transcript
    cache exists to avoid."""

    TAIL_BYTES: ClassVar[int] = 512_000
    """How much of a rollout's tail is searched. The block is written on token
    counts, so the newest one is at the end; reading a multi-megabyte session in
    full to find its last line is pure waste."""

    def __init__(self, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock

    # ─── local evidence, then the deliberately unsupported remote path ──────

    def describe(self, account: QuotaAccount) -> BillingAccountView:
        """The account's posture from its rollouts. Never network I/O."""
        snapshot = self.latest_snapshot(account.root)
        if snapshot is None:
            return self.failure(
                account,
                status="unsupported",
                detail="no recent Codex session under this profile recorded a rate-limit window",
            )
        now = self._clock()
        windows = self.parse_windows(snapshot.payload, observed_at=snapshot.observed_at)
        if not windows:  # pragma: no cover - latest_snapshot only returns usable blocks
            return self.failure(
                account,
                status="unsupported",
                detail="no recent Codex session under this profile recorded a rate-limit window",
            )
        rolled_over = all(w.resets_at is not None and w.resets_at <= now for w in windows)
        stale = rolled_over or snapshot.observed_at is None
        return BillingAccountView(
            account_id=account.account_id,
            provider=account.provider,
            label=account.label,
            billing_mode="subscription",
            subscription=self.parse_subscription(snapshot.payload),
            status="stale" if stale else "ok",
            detail=(
                "every recorded window has reset since this session ran; "
                "run Codex on this profile for a current reading"
                if stale
                else None
            ),
            windows=windows,
            observed_at=snapshot.observed_at,
            stale_seconds=(observed_age_seconds(snapshot.observed_at, now) if stale else None),
        )

    def collect(self, account: QuotaAccount) -> BillingAccountView:
        """Return local Codex evidence; credential refresh has no safe contract.

        The existing Codex integration exposes rollout data and a profile root,
        but no stable quota endpoint, credential schema, or configured issuer
        for a direct request.  Guessing any of those would risk sending a login
        credential to an uncontracted destination, so a profile with no local
        rate-limit block degrades explicitly instead of fabricating a remote
        refresh path.  This remains a distinct seam for when Codex publishes a
        supported contract.
        """
        view = self.describe(account)
        if view.status == "unsupported":
            return view.model_copy(
                update={
                    "detail": (
                        "no recent Codex rate-limit window is available; credential-backed "
                        "quota refresh is unsupported because this integration has no stable "
                        "endpoint or credential contract"
                    )
                }
            )
        return view

    # ─── pure normalization ─────────────────────────────────────────────────

    @classmethod
    def parse_windows(
        cls, payload: object, *, observed_at: datetime | None
    ) -> tuple[SubscriptionWindowView, ...]:
        """A ``rate_limits`` block → windows, in the provider's own key order.

        Every member whose value looks like a window is parsed, so a third key
        alongside ``primary``/``secondary`` needs no change here. A member with
        no ``used_percent`` is dropped: the envelope is written on every token
        count and an empty one is not a reading of zero.
        """
        if not isinstance(payload, dict):
            return ()
        windows: list[SubscriptionWindowView] = []
        for key, value in payload.items():
            if not isinstance(value, dict):
                continue
            used = as_float(value.get("used_percent"))
            if used is None:
                continue
            window_seconds = cls._window_seconds(value.get("window_minutes"))
            windows.append(
                SubscriptionWindowView(
                    scope=cls.scope_for_window(window_seconds),
                    label=str(key),
                    window_seconds=window_seconds,
                    used_percent=used,
                    remaining_percent=cls.remaining_percent(used),
                    resets_at=parse_epoch(value.get("resets_at")),
                    observed_at=observed_at,
                    evidence="rollout",
                )
            )
        return tuple(windows)

    @staticmethod
    def parse_subscription(payload: object) -> SubscriptionTier | None:
        """The plan the server named on this ``rate_limits`` block. Pure, total.

        Censused across **194 on-host rollouts / 53,286 ``rate_limits`` records**
        on **2026-08-11**, codex-cli 0.147.0 and older: ``plan_type`` sits inside
        the same block as the windows and takes the values ``plus`` (2,413),
        ``prolite`` (2,331), ``team`` (778) and ``null`` (47,764). It is the
        server's own word, so Grove passes it through rather than translating it
        into a name from ChatGPT's pricing page.

        **The plan is read from the SAME block as the windows, and that is the
        whole rule.** This host's corpus runs ``plus`` → ``team`` → ``prolite``
        over six months, so scanning back for the newest block that happens to
        name a plan would confidently report a plan the account left in March.
        A block whose ``plan_type`` is null answers ``None`` — 5,584 of the
        11,105 window-bearing blocks are exactly that, and no sub-tier field
        exists here at all.
        """
        if not isinstance(payload, dict):
            return None
        return subscription_tier(payload.get("plan_type"))

    @staticmethod
    def _window_seconds(raw: object) -> int | None:
        """``window_minutes`` → seconds, preserving whatever the provider said.

        No rounding and no snapping to a nominal boundary: the 10081-minute
        windows on this host are the provider's own answer, and a parser that
        tidied them into 10080 would be inventing precision.
        """
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        seconds = int(raw * 60)
        return seconds if seconds > 0 else None

    # ─── edges ──────────────────────────────────────────────────────────────

    def latest_snapshot(self, root: Path) -> _RateLimitSnapshot | None:
        """The newest usable ``rate_limits`` block under a profile root.

        Newest-file-first, and within a file newest-line-first, stopping at the
        first block that actually carries a window. Bounded twice
        (:attr:`MAX_ROLLOUTS`, :attr:`TAIL_BYTES`) because this runs on a page
        load and a profile can hold hundreds of multi-megabyte rollouts.
        """
        for path in self._recent_rollouts(root):
            snapshot = self._scan(path)
            if snapshot is not None:
                return snapshot
        return None

    def _recent_rollouts(self, root: Path) -> list[Path]:
        """Rollout files under ``root``, newest by mtime, capped.

        Sorted by mtime rather than by the timestamp in the filename: a resumed
        session keeps its original name while still being the file that just
        received the current window.
        """
        sessions = root / "sessions"
        try:
            candidates = [(p.stat().st_mtime, p) for p in sessions.rglob("rollout-*.jsonl")]
        except OSError:
            return []
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [path for _, path in candidates[: self.MAX_ROLLOUTS]]

    def _scan(self, path: Path) -> _RateLimitSnapshot | None:
        """The last usable block in one rollout's tail, or ``None``."""
        for record in self._tail_records(path):
            payload = record.get("payload")
            if not isinstance(payload, dict):
                continue
            block = payload.get("rate_limits")
            if not isinstance(block, dict):
                continue
            if not self.parse_windows(block, observed_at=None):
                continue
            return _RateLimitSnapshot(
                observed_at=parse_iso_datetime(record.get("timestamp")), payload=block
            )
        return None

    def _tail_lines(self, path: Path) -> list[str]:
        """The tail of ``path`` as lines, dropping a partial leading one.

        A byte offset lands inside a line, and half a line is not JSON — so a
        read that started mid-file discards its first line. Nothing else here
        can distinguish that case later.
        """
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                start = max(0, handle.tell() - self.TAIL_BYTES)
                handle.seek(start)
                chunk = handle.read()
        except OSError:
            return []
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        return lines[1:] if start > 0 and lines else lines

    def _tail_records(self, path: Path) -> Iterator[dict[str, Any]]:
        """Decoded rate-limit-bearing records from a file's tail, newest first.

        Only lines carrying the marker are parsed at all — see :data:`_MARKER`.
        """
        lines = self._tail_lines(path)
        for line in reversed(lines):
            if _MARKER not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                yield record


__all__ = ["CodexQuotaProvider"]
