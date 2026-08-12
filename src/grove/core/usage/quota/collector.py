"""QuotaCollector — refresh explicitly selected accounts and never raise.

The one public entry point of this package. It owns four things no individual
provider can: **which accounts are selected** (the quota config allowlist),
**when an account may be re-read** (TTL plus a growing cool-off), **what a caller
sees when a read fails** (last-known-good data, marked stale, never a blank
page), and **how little all of that costs the provider** (one upstream request
per account per window, shared across every Grove process on the host).

Synchronous and blocking by design. The daemon runs it off its event loop, the
same way it runs every other subprocess-and-network verb, and a provider that
had to be async would drag an event loop into a package the CLI also calls.

**No method here raises.** A failure is a :class:`BillingAccountView` with the
matching :class:`QuotaStatus` and one written sentence of ``detail``. That is
not defensive habit: this feeds a page that also renders session analytics, and
one expired login must never be able to take the rest of the page down with it.
The per-account guard is deliberately a bare ``Exception`` — a provider is
expected to return its failures, so anything that escapes is by definition
something nobody anticipated, and the page still has to answer.

**A read must be cheap enough that reading is never the thing that breaks it.**
Everything below exists because a quota page that probes per page load teaches a
provider to refuse it: the TTL, the persisted ledger that survives a restart,
the doubling cool-off, and the in-process coalescing that keeps two simultaneous
callers to one request. The whole decision layer is
:class:`~grove.core.usage.quota._state.QuotaProbeState`, which is pure and takes
its clock from here; this class only supplies the I/O and the config.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock

import httpx
from loguru import logger

from grove.core.config import GroveConfig
from grove.core.contracts.usage import BillingAccountView
from grove.core.usage.quota._state import QuotaProbeState, QuotaStateFile
from grove.core.usage.quota.base import QuotaAccount, QuotaProvider
from grove.core.usage.quota.claude import ClaudeQuotaProvider
from grove.core.usage.quota.codex import CodexQuotaProvider


class QuotaCollector:
    """Every reachable billing account's quota, cached and isolated per account.

    ``clock`` is the test seam for TTL and staleness; ``providers``, ``transport``
    and ``state_file`` inject the provider set, the HTTP transport and the
    durable ledger so no test ever reaches a socket or the user's state dir. All
    four default to the production wiring.
    """

    def __init__(
        self,
        *,
        cfg: GroveConfig,
        clock: Callable[[], datetime] | None = None,
        providers: Sequence[QuotaProvider] | None = None,
        transport: httpx.BaseTransport | None = None,
        state_file: QuotaStateFile | None = None,
    ) -> None:
        self._cfg = cfg
        self._clock = clock or (lambda: datetime.now(UTC))
        quota = cfg.usage.quota
        self._providers: tuple[QuotaProvider, ...] = tuple(
            providers
            if providers is not None
            else (
                ClaudeQuotaProvider(
                    timeout=quota.timeout_seconds, clock=self._clock, transport=transport
                ),
                CodexQuotaProvider(clock=self._clock),
            )
        )
        self._state_file = state_file if state_file is not None else QuotaStateFile()
        self._states: dict[str, QuotaProbeState] = self._state_file.load()
        # Counts COMPLETED upstream probes, per account, for the life of this
        # process. It is the single-flight token: a caller records the count it
        # saw before queueing on the lock, and a probe that landed while it
        # waited is that caller's answer too. A timestamp cannot serve here —
        # under an injected clock two calls share one instant, and in production
        # two probes can share a millisecond.
        self._probes: dict[str, int] = {}
        # One lock over the whole collection pass, so two clients opening the
        # page do not both fetch. Coarse on purpose: the pass is bounded by the
        # per-request timeout times the account count, and a per-account lock
        # would let the second caller start its own duplicate pass anyway.
        self._lock = RLock()

    # ─── public seam ────────────────────────────────────────────────────────

    def accounts(self) -> tuple[BillingAccountView, ...]:
        """Every selected account, from local evidence only. No network.

        The enumeration seam: a caller that wants to know *which* accounts exist
        — to render a filter, or to count them — must not pay for a round trip
        per account. An account with a persisted reading reports it (including
        one collected by a previous run of this daemon); one with none reports
        whatever its credential store or its rollouts can say on their own.
        """
        return self._answer(force=False, network=False)

    def snapshot(self) -> tuple[BillingAccountView, ...]:
        """Every account's current quota, served from cache within the TTL."""
        return self._answer(force=False, network=True)

    def refresh(self) -> tuple[BillingAccountView, ...]:
        """Re-read every selected account now, ignoring TTL but NOT the cool-off.

        A floor a caller can bypass is not a floor: the explicit-refresh button
        is exactly the thing a frustrated operator presses repeatedly at an
        account that is rate-limited, which is the one situation in which extra
        requests make it worse.
        """
        return self._answer(force=True, network=True)

    def close(self) -> None:
        """Release every provider's transport."""
        for provider in self._providers:
            provider.close()

    # ─── orchestration ──────────────────────────────────────────────────────

    def _answer(self, *, force: bool, network: bool) -> tuple[BillingAccountView, ...]:
        """Resolve every selected account, isolating each one's failure."""
        if not self._cfg.usage.quota.enabled:
            return ()
        selected = self._selected_accounts()
        # Read BEFORE queueing on the lock. Anything that lands while this
        # caller waits is a probe it would otherwise duplicate, and the whole
        # point of coalescing is that the waiter gets the winner's answer.
        seen = {
            account.account_id: self._probes.get(account.account_id, 0) for _, account in selected
        }
        with self._lock:
            views: list[BillingAccountView] = []
            written: dict[str, QuotaProbeState] = {}
            for provider, account in selected:
                view = self._resolve(
                    provider,
                    account,
                    force=force,
                    network=network,
                    probes_seen=seen[account.account_id],
                    written=written,
                )
                views.append(view)
            self._state_file.merge(written)
            return tuple(views)

    def _resolve(
        self,
        provider: QuotaProvider,
        account: QuotaAccount,
        *,
        force: bool,
        network: bool,
        probes_seen: int,
        written: dict[str, QuotaProbeState],
    ) -> BillingAccountView:
        """One account: cached answer, cool-off, or a fresh read. Never raises."""
        now = self._clock()
        quota = self._cfg.usage.quota
        state = self._states.get(account.account_id, QuotaProbeState()).relabelled(account.label)

        if not network:
            # ``accounts()`` is an enumeration read, not a collection. Its local
            # answer is never recorded: caching a "nothing collected yet" as
            # though it were a reading would make the first real snapshot() treat
            # that non-answer as fresh and skip the provider request altogether.
            cached = state.render(now)
            if cached is not None:
                return cached
            return self._describe(provider, account)

        # The TTL is a rate-limit budget, so it governs only the providers that
        # spend one. An unmetered read is a file the tool already wrote: it can
        # never be refused, so withholding it inside a window ages an answer
        # that costs nothing to take. The cool-off below is deliberately NOT
        # gated the same way — it fires on what a provider actually returned,
        # so a provider that unexpectedly starts refusing still backs off.
        ttl_seconds = quota.ttl_seconds if provider.metered else 0
        coalesced = self._probes.get(account.account_id, 0) > probes_seen
        if not state.may_probe(now, ttl_seconds=ttl_seconds, force=force and not coalesced):
            served = state.render(now)
            if served is not None:
                return served

        view = self._collect(provider, account)
        state = state.record(
            view,
            now,
            floor_seconds=quota.retry_floor_seconds,
            max_seconds=quota.retry_max_seconds,
            retry_after_seconds=(
                (view.retry_after - now).total_seconds() if view.retry_after else None
            ),
        )
        self._states[account.account_id] = state
        self._probes[account.account_id] = self._probes.get(account.account_id, 0) + 1
        written[account.account_id] = state
        self._log_outcome(account, state)
        rendered = state.render(now)
        return rendered if rendered is not None else view

    def _collect(self, provider: QuotaProvider, account: QuotaAccount) -> BillingAccountView:
        """The one upstream read, with the unanticipated-exception guard on it."""
        try:
            return provider.collect(account)
        except Exception as exc:  # a provider returns its failures; see the module docstring
            logger.warning(
                "quota collection for {} failed unexpectedly: {}",
                account.account_id,
                type(exc).__name__,
            )
            return provider.failure(
                account,
                status="unreachable",
                detail="quota collection failed unexpectedly for this account",
            )

    def _describe(self, provider: QuotaProvider, account: QuotaAccount) -> BillingAccountView:
        """Local-evidence enumeration, best-effort for the same reason."""
        try:
            return provider.describe(account)
        except Exception as exc:  # keep account enumeration best-effort
            logger.warning(
                "quota description for {} failed unexpectedly: {}",
                account.account_id,
                type(exc).__name__,
            )
            return provider.failure(
                account,
                status="unreachable",
                detail="quota description failed unexpectedly for this account",
            )

    @staticmethod
    def _log_outcome(account: QuotaAccount, state: QuotaProbeState) -> None:
        """One structured line per PROBE — never per render.

        Edge-triggered on purpose: a line per served snapshot would be noise
        proportional to page loads, and the question an operator actually has
        ("how often are we contacting this provider, and what did it say") is
        answered only by the probes. Before this existed there was no way to
        count Grove's own upstream requests after the fact, which is what made
        the rate limit hard to attribute.
        """
        status = state.last_view.status if state.last_view else "unknown"
        if state.retry_not_before is not None:
            logger.warning(
                "quota probe {} -> {}; backing off until {} after {} consecutive failures",
                account.account_id,
                status,
                state.retry_not_before.isoformat(timespec="seconds"),
                state.consecutive_failures,
            )
            return
        logger.debug("quota probe {} -> {}", account.account_id, status)

    # ─── selection ──────────────────────────────────────────────────────────

    def _selected_accounts(self) -> tuple[tuple[QuotaProvider, QuotaAccount], ...]:
        """Configured (provider, profile root) pairs, deduped by account id.

        Quota is deliberately opt-in per profile. Transcript activity has its
        own broad discovery path; reusing it here would silently contact and
        display ambient/API profiles the operator never selected.
        """
        labels = self._cfg.usage.quota.labels
        found: dict[str, tuple[QuotaProvider, QuotaAccount]] = {}
        providers = {provider.provider: provider for provider in self._providers}
        for provider_name, roots in self._cfg.usage.quota.profiles.items():
            provider = providers.get(provider_name)
            if provider is None:
                continue
            for root in roots:
                account = QuotaAccount.mint(
                    provider=provider_name,
                    root=Path(root),
                    labels=labels,
                )
                found.setdefault(account.account_id, (provider, account))
        return tuple(sorted(found.values(), key=lambda pair: (pair[1].provider, pair[1].label)))


__all__ = ["QuotaCollector"]
