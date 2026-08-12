"""Quota collection regressions: cache semantics, retry floors, and discovery."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from grove.core.config import GroveConfig
from grove.core.contracts.usage import BillingAccountView, SubscriptionWindowView, UsageProvider
from grove.core.usage.quota import CodexQuotaProvider, QuotaAccount, QuotaCollector, QuotaProvider
from grove.core.usage.quota._state import QuotaProbeState, QuotaStateFile
from grove.core.usage.quota.base import parse_retry_after


@dataclass
class _Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


class _Provider(QuotaProvider):
    """A provider whose collection responses are controlled by each test."""

    def __init__(
        self,
        *,
        provider: UsageProvider,
        root: Path,
        collected: list[BillingAccountView],
        metered: bool = True,
    ) -> None:
        self.provider = provider
        self.metered = metered
        self._root = root
        self._collected = list(collected)
        self.collect_calls = 0
        self.on_collect: Callable[[], None] | None = None

    def describe(self, account: QuotaAccount) -> BillingAccountView:
        return self.failure(
            account,
            status="unsupported",
            detail="no local quota snapshot has been collected",
            billing_mode="subscription",
        )

    def collect(self, account: QuotaAccount) -> BillingAccountView:
        """Answer the next scripted view, REPEATING the last once exhausted.

        Repeating rather than raising is what lets a test assert on a condition
        that persists — a limit nobody has lifted answers the same way however
        many times it is asked, which is the case every backoff assertion is
        about.
        """
        if self.on_collect is not None:
            self.on_collect()
        view = self._collected[min(self.collect_calls, len(self._collected) - 1)]
        self.collect_calls += 1
        return view.model_copy(update={"account_id": account.account_id, "label": account.label})


def _cfg(
    *,
    root: Path | None = None,
    provider: UsageProvider = "claude_code",
    retry_max_seconds: int = 3600,
) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "usage": {
                "quota": {
                    "ttl_seconds": 30,
                    "retry_floor_seconds": 120,
                    "retry_max_seconds": retry_max_seconds,
                    "profiles": ({provider: [str(root)]} if root is not None else {}),
                }
            }
        }
    )


def _view(
    *,
    status: str,
    observed_at: datetime | None = None,
    used_percent: float | None = None,
) -> BillingAccountView:
    """A provider answer. A measured one CARRIES A WINDOW, as production's do.

    Neither real provider can emit a windowless ``ok``: Claude returns
    ``unsupported`` when ``limits[]`` yields nothing and Codex only reports a
    status at all once it has parsed a block. A fixture that emits one describes
    a program Grove does not run — and it hid the rule that decides what
    last-known-good even means, which is whether the answer measured anything.
    """
    measured = status in {"ok", "stale"}
    return BillingAccountView(
        account_id="will-be-replaced",
        provider="claude_code",
        label="will-be-replaced",
        billing_mode="subscription",
        status=status,  # type: ignore[arg-type]
        detail=f"{status} response",
        observed_at=observed_at,
        windows=(
            (
                SubscriptionWindowView(
                    scope="session",
                    label="session",
                    used_percent=used_percent if used_percent is not None else 12.0,
                    remaining_percent=100.0 - (used_percent if used_percent is not None else 12.0),
                    observed_at=observed_at,
                    evidence="provider_endpoint",
                ),
            )
            if measured
            else ()
        ),
    )


def test_accounts_does_not_cache_unsupported_local_description(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    provider = _Provider(
        provider="claude_code", root=tmp_path / "profile", collected=[_view(status="ok")]
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"), clock=clock, providers=(provider,)
    )

    assert collector.accounts()[0].status == "unsupported"
    assert provider.collect_calls == 0

    assert collector.snapshot()[0].status == "ok"
    assert provider.collect_calls == 1


@pytest.mark.parametrize("failure_status", ["auth_expired", "rate_limited"])
def test_stale_last_good_keeps_original_failure_retry_floor(
    tmp_path: Path, failure_status: str
) -> None:
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    provider = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[
            _view(status="ok", observed_at=clock.now),
            _view(status=failure_status),
            _view(status="ok", observed_at=clock.now),
        ],
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"), clock=clock, providers=(provider,)
    )

    assert collector.snapshot()[0].status == "ok"
    stale = collector.refresh()[0]

    assert stale.status == "stale"
    assert stale.detail == f"{failure_status} response"
    assert provider.collect_calls == 2

    clock.advance(1)
    assert collector.refresh()[0].status == "stale"
    assert provider.collect_calls == 2


def test_stale_last_good_keeps_unknown_age_null(tmp_path: Path) -> None:
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    provider = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[_view(status="ok"), _view(status="unreachable")],
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"), clock=clock, providers=(provider,)
    )

    collector.snapshot()
    stale = collector.refresh()[0]

    assert stale.status == "stale"
    assert stale.stale_seconds is None


def test_collection_includes_only_explicitly_selected_profiles(tmp_path: Path) -> None:
    selected = tmp_path / "selected-codex"
    ambient = tmp_path / "ambient-codex"
    provider = _Provider(provider="codex", root=ambient, collected=[])
    collector = QuotaCollector(
        cfg=_cfg(root=selected, provider="codex"),
        providers=(provider,),
    )

    accounts = collector.accounts()

    assert len(accounts) == 1
    assert accounts[0].account_id == QuotaAccount.mint(provider="codex", root=selected).account_id
    assert accounts[0].account_id != QuotaAccount.mint(provider="codex", root=ambient).account_id


def test_empty_profile_selection_collects_and_displays_no_quota(tmp_path: Path) -> None:
    provider = _Provider(provider="claude_code", root=tmp_path / "ambient", collected=[])
    collector = QuotaCollector(cfg=_cfg(), providers=(provider,))

    assert collector.accounts() == ()
    assert collector.snapshot() == ()
    assert provider.collect_calls == 0


def test_quota_profile_config_rejects_unsupported_providers() -> None:
    assert GroveConfig().usage.quota.profiles == {}
    with pytest.raises(ValidationError):
        GroveConfig.model_validate(
            {"usage": {"quota": {"profiles": {"generic": ["/tmp/profile"]}}}}
        )
    with pytest.raises(ValidationError):
        GroveConfig.model_validate({"usage": {"quota": {"profiles": {"claude_code": [""]}}}})


def test_selected_profiles_deduplicate_equivalent_roots(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(selected, target_is_directory=True)
    cfg = GroveConfig.model_validate(
        {"usage": {"quota": {"profiles": {"claude_code": [str(selected), str(alias)]}}}}
    )
    provider = _Provider(provider="claude_code", root=tmp_path / "unused", collected=[])

    assert len(QuotaCollector(cfg=cfg, providers=(provider,)).accounts()) == 1


def test_codex_without_rollout_has_precise_credential_refresh_degradation(tmp_path: Path) -> None:
    provider = CodexQuotaProvider(clock=lambda: datetime(2026, 8, 9, tzinfo=UTC))
    account = QuotaAccount.mint(provider="codex", root=tmp_path / "codex-profile")

    view = provider.collect(account)

    assert view.status == "unsupported"
    assert view.detail is not None
    assert "credential-backed quota refresh is unsupported" in view.detail


# ─── how often Grove is allowed to contact a provider ───────────────────────


def test_a_read_within_the_ttl_costs_no_upstream_request(tmp_path: Path) -> None:
    """Many readers inside one window are one request.

    The regression this pins is the whole first half of the incident: a quota
    page that probes per page load teaches the provider to refuse it.
    """
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    provider = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[_view(status="ok", observed_at=clock.now)],
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"), clock=clock, providers=(provider,)
    )

    for _ in range(10):
        assert collector.snapshot()[0].status == "ok"
    assert provider.collect_calls == 1

    clock.advance(29)
    collector.snapshot()
    assert provider.collect_calls == 1

    clock.advance(1)  # ttl_seconds=30 has now elapsed
    collector.snapshot()
    assert provider.collect_calls == 2


def test_an_unmetered_provider_is_never_held_behind_the_ttl(tmp_path: Path) -> None:
    """A file read costs nobody a request, so caching it only ages it.

    The two providers have opposite cost shapes and the machinery in this
    package exists entirely for the expensive one. Generalizing the TTL to both
    means every step taken to protect a rate limiter is silently paid for in
    staleness by a provider that has no limiter to protect.
    """
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    local = _Provider(
        provider="codex",
        root=tmp_path / "profile",
        collected=[_view(status="ok", observed_at=clock.now)],
        metered=False,
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile", provider="codex"),
        clock=clock,
        providers=(local,),
        state_file=QuotaStateFile(tmp_path / "quota-state.json"),
    )

    for _ in range(3):
        assert collector.snapshot()[0].status == "ok"
    assert local.collect_calls == 3


def test_an_unmetered_provider_that_starts_refusing_still_backs_off(tmp_path: Path) -> None:
    """The cool-off keys off what came BACK, never off the declared cost shape.

    Skipping the TTL for an unmetered provider must not also hand it an
    unlimited retry budget: the gate that matters is the provider's own answer,
    which is the one signal that can change without anyone re-declaring it.
    """
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    local = _Provider(
        provider="codex",
        root=tmp_path / "profile",
        collected=[_view(status="rate_limited")],
        metered=False,
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile", provider="codex"),
        clock=clock,
        providers=(local,),
        state_file=QuotaStateFile(tmp_path / "quota-state.json"),
    )

    collector.snapshot()
    clock.advance(60)
    collector.snapshot()

    assert local.collect_calls == 1


def test_concurrent_readers_coalesce_onto_one_probe(tmp_path: Path) -> None:
    """Eight simultaneous forced refreshes make ONE upstream request.

    Serializing on the lock is not enough on its own: every waiter arrives with
    ``force`` set and would each bypass the TTL in turn. A caller coalesces onto
    a probe that COMPLETED while it waited, which is what makes eight browser
    tabs opening at once cost one request rather than eight.
    """
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    provider = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[_view(status="ok", observed_at=clock.now)],
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"), clock=clock, providers=(provider,)
    )
    probing, release = threading.Event(), threading.Event()

    def _hold() -> None:
        probing.set()
        release.wait(timeout=5)

    provider.on_collect = _hold

    first = threading.Thread(target=collector.refresh)
    first.start()
    assert probing.wait(timeout=5)  # the one probe is now open and blocking
    waiters = [threading.Thread(target=collector.refresh) for _ in range(7)]
    for waiter in waiters:
        waiter.start()
    # The waiters read their coalescing token BEFORE queueing on the lock, and
    # `start()` only guarantees the thread exists. This closes that one window;
    # the decision it exercises is asserted without threads or sleeping in
    # `test_the_cool_off_outranks_an_explicit_refresh` and its siblings.
    time.sleep(0.05)
    release.set()
    for thread in (first, *waiters):
        thread.join(timeout=5)

    assert provider.collect_calls == 1


# ─── what survives a failed read ────────────────────────────────────────────


def test_rate_limited_read_serves_last_known_good_with_its_own_observed_at(
    tmp_path: Path,
) -> None:
    """The measured defect: a 429 must not erase what Grove already knew."""
    clock = _Clock(datetime(2026, 8, 9, 10, 33, tzinfo=UTC))
    good_at = clock.now
    provider = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[
            _view(status="ok", observed_at=good_at, used_percent=34.0),
            _view(status="rate_limited"),
        ],
    )
    collector = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"), clock=clock, providers=(provider,)
    )

    assert collector.snapshot()[0].status == "ok"
    clock.advance(2 * 3600 + 14 * 60)  # the 10:33 -> 12:47 gap, to the minute

    served = collector.refresh()[0]

    assert served.status == "stale"
    assert served.last_error == "rate_limited"
    assert [w.used_percent for w in served.windows] == [34.0]
    assert served.observed_at == good_at
    assert served.stale_seconds == 2 * 3600 + 14 * 60
    assert served.retry_after == clock.now + timedelta(seconds=120)


def test_last_known_good_survives_a_daemon_restart(tmp_path: Path) -> None:
    """A second collector over the same ledger answers with the first's reading.

    This is the root cause of the reported data loss: the reading collected at
    10:33 was in process memory, the daemon was restarted at 11:39 and 11:45,
    and the failing read that followed had nothing to fall back to. The ledger
    is what makes a restart lose nothing.
    """
    ledger = QuotaStateFile(tmp_path / "quota-state.json")
    clock = _Clock(datetime(2026, 8, 9, 10, 33, tzinfo=UTC))
    good_at = clock.now
    first = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[_view(status="ok", observed_at=good_at, used_percent=34.0)],
    )
    QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"),
        clock=clock,
        providers=(first,),
        state_file=ledger,
    ).snapshot()

    clock.advance(3600)
    restarted = _Provider(
        provider="claude_code",
        root=tmp_path / "profile",
        collected=[_view(status="rate_limited")],
    )
    served = QuotaCollector(
        cfg=_cfg(root=tmp_path / "profile"),
        clock=clock,
        providers=(restarted,),
        state_file=ledger,
    ).snapshot()[0]

    assert served.status == "stale"
    assert served.observed_at == good_at
    assert [w.used_percent for w in served.windows] == [34.0]
    assert served.stale_seconds == 3600


def test_a_fresh_process_inside_the_ttl_makes_no_request_at_all(tmp_path: Path) -> None:
    """The ledger is a shared probe budget, not just a memory.

    Three Grove processes (daemon, TUI, a `grove` CLI run) reading quota inside
    one window is one upstream request, not three — which is what the restart
    loop was quietly paying before.
    """
    ledger = QuotaStateFile(tmp_path / "quota-state.json")
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    views = [_view(status="ok", observed_at=clock.now)]
    providers = [
        _Provider(provider="claude_code", root=tmp_path / "profile", collected=views)
        for _ in range(3)
    ]
    for provider in providers:
        QuotaCollector(
            cfg=_cfg(root=tmp_path / "profile"),
            clock=clock,
            providers=(provider,),
            state_file=ledger,
        ).snapshot()

    assert [p.collect_calls for p in providers] == [1, 0, 0]


def test_the_ledger_counts_upstream_reads_across_restarts(tmp_path: Path) -> None:
    """How often Grove probed is answerable after the fact, without a log.

    The per-probe log line is a `debug` and Grove's default sink is `WARNING`,
    so a healthy probe leaves nothing behind on an ordinary install and the one
    question the instrumentation exists to answer — is Grove the one knocking —
    could only be reconstructed from failures. The count is on the ledger, so it
    survives the restarts that erased everything else.
    """
    ledger = QuotaStateFile(tmp_path / "quota-state.json")
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    account_id = QuotaAccount.mint(provider="claude_code", root=tmp_path / "profile").account_id

    for _ in range(2):
        QuotaCollector(
            cfg=_cfg(root=tmp_path / "profile"),
            clock=clock,
            providers=(
                _Provider(
                    provider="claude_code",
                    root=tmp_path / "profile",
                    collected=[_view(status="ok", observed_at=clock.now)],
                ),
            ),
            state_file=ledger,
        ).snapshot()
        clock.advance(30)  # ttl_seconds=30, so the second process really probes

    assert ledger.load()[account_id].probe_count == 2


def test_an_unreadable_ledger_degrades_to_no_history(tmp_path: Path) -> None:
    """A corrupt ledger costs a head start, never a page."""
    path = tmp_path / "quota-state.json"
    path.write_text("{not json", encoding="utf-8")

    assert QuotaStateFile(path).load() == {}


def test_the_ledger_keeps_accounts_this_process_never_selected(tmp_path: Path) -> None:
    """A narrower selection must not delete another process's history.

    The `grove` CLI legitimately runs with a different ``usage.quota.profiles``
    than the daemon; a replace-on-write would let it wipe readings for accounts
    it was never asked about.
    """
    ledger = QuotaStateFile(tmp_path / "quota-state.json")
    ledger.merge({"other-account": QuotaProbeState(consecutive_failures=2)})
    ledger.merge({"mine": QuotaProbeState(consecutive_failures=1)})

    loaded = ledger.load()

    assert set(loaded) == {"other-account", "mine"}
    assert loaded["other-account"].consecutive_failures == 2


# ─── the cool-off, as pure arithmetic ───────────────────────────────────────


def test_repeated_rate_limits_back_off_by_doubling_up_to_the_cap() -> None:
    """A fixed floor against an unlifted limit is still a knock every floor."""
    now = datetime(2026, 8, 9, tzinfo=UTC)
    state = QuotaProbeState()
    waits: list[float] = []

    for _ in range(8):
        state = state.record(_view(status="rate_limited"), now, floor_seconds=120, max_seconds=1800)
        assert state.retry_not_before is not None
        waits.append((state.retry_not_before - now).total_seconds())

    assert waits == [120, 240, 480, 960, 1800, 1800, 1800, 1800]


def test_a_successful_read_resets_the_backoff() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)
    state = QuotaProbeState()
    for _ in range(4):
        state = state.record(_view(status="rate_limited"), now, floor_seconds=120, max_seconds=3600)

    state = state.record(
        _view(status="ok", observed_at=now), now, floor_seconds=120, max_seconds=3600
    )

    assert state.consecutive_failures == 0
    assert state.retry_not_before is None


def test_a_provider_asking_for_longer_wins_and_asking_for_less_does_not() -> None:
    """``Retry-After`` bounds when the limit lifts, not how often to ask."""
    now = datetime(2026, 8, 9, tzinfo=UTC)
    patient = QuotaProbeState().record(
        _view(status="rate_limited"),
        now,
        floor_seconds=120,
        max_seconds=3600,
        retry_after_seconds=900,
    )
    impatient = QuotaProbeState().record(
        _view(status="rate_limited"),
        now,
        floor_seconds=120,
        max_seconds=3600,
        retry_after_seconds=5,
    )

    assert patient.retry_not_before == now + timedelta(seconds=900)
    assert impatient.retry_not_before == now + timedelta(seconds=120)


def test_the_cool_off_outranks_an_explicit_refresh() -> None:
    """A floor a caller can bypass is not a floor."""
    now = datetime(2026, 8, 9, tzinfo=UTC)
    state = QuotaProbeState().record(
        _view(status="rate_limited"), now, floor_seconds=120, max_seconds=3600
    )

    assert not state.may_probe(now + timedelta(seconds=119), ttl_seconds=30, force=True)
    assert state.may_probe(now + timedelta(seconds=120), ttl_seconds=30, force=True)


def test_a_never_collected_account_may_always_probe() -> None:
    now = datetime(2026, 8, 9, tzinfo=UTC)

    assert QuotaProbeState().may_probe(now, ttl_seconds=30, force=False)


def test_a_network_error_does_not_earn_a_cool_off() -> None:
    """It clears on its own, and the TTL already bounds how often it is retried."""
    now = datetime(2026, 8, 9, tzinfo=UTC)

    state = QuotaProbeState().record(
        _view(status="unreachable"), now, floor_seconds=120, max_seconds=3600
    )

    assert state.retry_not_before is None
    assert state.may_probe(now + timedelta(seconds=30), ttl_seconds=30, force=False)


def test_a_newer_rolled_over_reading_is_not_replaced_by_an_older_good_one() -> None:
    """Codex reports ``stale`` WITH the newest numbers Grove has.

    Keying last-known-good on the status word would file that newer reading as a
    failure and answer with the older one — this class's own bug, inverted.
    """
    now = datetime(2026, 8, 9, tzinfo=UTC)
    older = _view(status="ok", observed_at=now, used_percent=10.0)
    newer = _view(status="stale", observed_at=now + timedelta(hours=1), used_percent=80.0)

    state = QuotaProbeState().record(older, now, floor_seconds=120, max_seconds=3600)
    state = state.record(newer, now + timedelta(hours=1), floor_seconds=120, max_seconds=3600)
    rendered = state.render(now + timedelta(hours=1))

    assert rendered is not None
    assert [w.used_percent for w in rendered.windows] == [80.0]
    assert rendered.last_error is None


# ─── the provider's own instruction ─────────────────────────────────────────


def test_retry_after_accepts_both_forms_the_spec_allows() -> None:
    """A client handling only delta-seconds ignores every server that dates it —
    and an ignored instruction is indistinguishable from one never sent."""
    now = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

    assert parse_retry_after("120", now) == now + timedelta(seconds=120)
    assert parse_retry_after("Sun, 09 Aug 2026 12:05:00 GMT", now) == datetime(
        2026, 8, 9, 12, 5, tzinfo=UTC
    )
    assert parse_retry_after("Sun, 09 Aug 2026 11:00:00 GMT", now) is None  # already past
    assert parse_retry_after("soon", now) is None
    assert parse_retry_after(None, now) is None
    assert parse_retry_after("", now) is None


def test_the_real_claude_provider_carries_a_429_retry_after_into_the_cool_off(
    tmp_path: Path,
) -> None:
    """Drive the REAL provider, not the double, over a scripted transport.

    Every other backoff assertion here goes through ``_Provider``, which cannot
    disagree with production about a header it never reads. This one proves the
    whole chain connects: response header -> ``BillingAccountView.retry_after``
    -> the collector's cool-off -> a refresh that makes no second request.
    """
    root = tmp_path / "claude-profile"
    root.mkdir()
    (root / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "test-token"}}', encoding="utf-8"
    )
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    requests = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"retry-after": "1800"}, json={})

    collector = QuotaCollector(
        cfg=_cfg(root=root),
        clock=clock,
        transport=httpx.MockTransport(_handler),
        state_file=QuotaStateFile(tmp_path / "quota-state.json"),
    )

    view = collector.snapshot()[0]

    assert view.status == "rate_limited"
    assert view.last_error == "rate_limited"
    assert view.retry_after == clock.now + timedelta(seconds=1800)

    clock.advance(1799)  # far past the 120 s floor Grove would have chosen
    assert collector.refresh()[0].retry_after == view.retry_after
    assert requests == 1

    clock.advance(1)
    collector.refresh()
    assert requests == 2


def test_a_provider_saying_retry_after_zero_does_not_produce_a_hot_loop(tmp_path: Path) -> None:
    """Measured against the live endpoint on 2026-08-10: a 429 there carries
    ``retry-after: 0``.

    Which is to say the header is present and meaningless, and a client that
    honours it literally answers a rate limit by retrying immediately, forever —
    strictly worse than having read no header at all. This is the whole reason
    the rule is *the provider wins only when it asks for LONGER*.
    """
    root = tmp_path / "claude-profile"
    root.mkdir()
    (root / ".credentials.json").write_text(
        '{"claudeAiOauth": {"accessToken": "test-token"}}', encoding="utf-8"
    )
    clock = _Clock(datetime(2026, 8, 9, tzinfo=UTC))
    requests = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(429, headers={"retry-after": "0"}, json={})

    collector = QuotaCollector(
        cfg=_cfg(root=root),
        clock=clock,
        transport=httpx.MockTransport(_handler),
        state_file=QuotaStateFile(tmp_path / "quota-state.json"),
    )

    view = collector.snapshot()[0]

    assert view.retry_after == clock.now + timedelta(seconds=120)
    for _ in range(20):
        clock.advance(5)
        collector.refresh()
    assert requests == 1
