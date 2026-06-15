"""Release-skew check: pure semver compare + best-effort cached fetcher (#80)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from grove.core.release import ReleaseChecker, ReleaseStatus, update_available

# ─── pure semver comparison ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("installed", "latest", "expected"),
    [
        ("0.1.0", "0.2.0", True),
        ("0.1.0", "v0.2.0", True),  # leading-v tag
        ("0.1.0", "0.1.1", True),
        ("0.1.0", "1.0.0", True),
        ("0.1.0", "0.1.0", False),  # equal → no nudge
        ("0.2.0", "0.1.0", False),  # dev ahead of last release → no nudge
        ("0.1.0", "0.1.0-rc1", False),  # pre-release of same core == same core
        ("0.1.0", "0.2.0-rc1", True),  # pre-release of a higher core still nudges
        ("0.1", "0.1.0", False),  # zero-padded equality
        ("0.1", "0.1.1", True),
        ("0.1.0", None, False),  # unknown latest → never nudge
        ("0.1.0", "garbage", False),  # unparseable → never nudge
        ("0.1.0", "", False),
    ],
)
def test_update_available(installed: str, latest: str | None, expected: bool) -> None:
    assert update_available(installed, latest) is expected


# ─── ReleaseChecker: best-effort, cached, never raises ───────────────────────


class _Clock:
    """Hand-advanced clock so the TTL is deterministic without sleeping."""

    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_check_reports_update_when_remote_is_newer() -> None:
    checker = ReleaseChecker(installed="0.1.0", fetcher=lambda: "v0.2.0")
    status = checker.check()
    assert status == ReleaseStatus(installed="0.1.0", latest="0.2.0", update_available=True)


def test_check_no_update_when_equal() -> None:
    checker = ReleaseChecker(installed="0.2.0", fetcher=lambda: "0.2.0")
    status = checker.check()
    assert status.latest == "0.2.0"
    assert status.update_available is False


def test_check_caches_within_ttl_and_refetches_after() -> None:
    clock = _Clock()
    calls = {"n": 0}

    def fetcher() -> str:
        calls["n"] += 1
        return "0.9.0"

    checker = ReleaseChecker(
        installed="0.1.0", fetcher=fetcher, ttl=timedelta(hours=6), clock=clock
    )
    checker.check()
    checker.check()
    assert calls["n"] == 1, "second call within the TTL must hit the cache"

    clock.now += timedelta(hours=7)
    checker.check()
    assert calls["n"] == 2, "a call past the TTL must re-fetch"


def test_check_swallows_fetch_failure_and_keeps_last_good() -> None:
    clock = _Clock()
    state = {"fail": False}

    def fetcher() -> str:
        if state["fail"]:
            raise RuntimeError("network down")
        return "0.5.0"

    checker = ReleaseChecker(
        installed="0.1.0", fetcher=fetcher, ttl=timedelta(hours=1), clock=clock
    )
    assert checker.check().latest == "0.5.0"

    # Next refresh fails — the last good latest stands, and check() never raises.
    state["fail"] = True
    clock.now += timedelta(hours=2)
    status = checker.check()
    assert status.latest == "0.5.0"
    assert status.update_available is True


def test_check_unknown_until_first_successful_fetch() -> None:
    """A failure before any success yields latest=None / update_available=False."""
    checker = ReleaseChecker(installed="0.1.0", fetcher=_raise)
    status = checker.check()
    assert status == ReleaseStatus(installed="0.1.0", latest=None, update_available=False)


def _raise() -> str:
    raise RuntimeError("boom")
