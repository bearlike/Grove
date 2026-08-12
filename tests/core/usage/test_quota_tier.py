"""Which subscription an account is on — and the cases where Grove must not say.

Both providers answer this from evidence they already read for something else:
Claude from the credential store it opens per collection, Codex from the very
``rate_limits`` block its windows come out of. So every payload here is shaped
like a real one captured on this host — see each provider's module docstring for
the census — and the assertions that matter are the negative ones. A plan Grove
reports wrongly is reconciled against somebody's bill; a plan Grove leaves
absent is a blank field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from grove.core.config import GroveConfig
from grove.core.contracts.usage import BillingAccountView
from grove.core.usage.quota import ClaudeQuotaProvider, CodexQuotaProvider, QuotaAccount
from grove.core.usage.quota._state import QuotaProbeState, QuotaStateFile
from grove.core.usage.quota.collector import QuotaCollector


@dataclass
class _Clock:
    now: datetime

    def __call__(self) -> datetime:
        return self.now


_NOW = datetime(2026, 8, 11, tzinfo=UTC)


def _claude_profile(tmp_path: Path, block: dict[str, object]) -> Path:
    root = tmp_path / "claude-profile"
    root.mkdir(exist_ok=True)
    (root / ".credentials.json").write_text(json.dumps({"claudeAiOauth": block}), encoding="utf-8")
    return root


def _codex_profile(tmp_path: Path, *records: dict[str, object]) -> Path:
    """A profile holding one rollout whose lines are ``records``, oldest first."""
    root = tmp_path / "codex-profile"
    day = root / "sessions" / "2026" / "08" / "11"
    day.mkdir(parents=True, exist_ok=True)
    (day / "rollout-2026-08-11T00-00-00-0000.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    return root


def _codex_record(
    *, timestamp: str, used_percent: float | None, plan_type: object
) -> dict[str, object]:
    """One ``token_count`` row in the shape codex-cli writes it.

    The envelope is written on every token count and filled only when the server
    said something, which is why ``used_percent`` and ``plan_type`` are
    independently absent here: 42,181 of 50,988 real records carry the block
    with no window at all.
    """
    primary = {"window_minutes": 10080, "resets_at": 1787024130}
    if used_percent is not None:
        primary["used_percent"] = used_percent
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": "codex",
                "primary": primary,
                "secondary": None,
                "plan_type": plan_type,
            },
        },
    }


# ─── Claude: the plan sits beside the token, so it costs no request ──────────


def test_claude_reads_the_plan_and_its_sub_tier_out_of_the_credential_store() -> None:
    """The shape captured on this host on 2026-08-11 (Claude Code 2.1.227)."""
    tier = ClaudeQuotaProvider.parse_subscription(
        {
            "accessToken": "irrelevant",
            "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
        }
    )

    assert tier is not None
    assert (tier.plan, tier.label, tier.detail) == ("max", "max", "20x")


@pytest.mark.parametrize(
    "block",
    [
        pytest.param({"accessToken": "irrelevant"}, id="no plan field at all"),
        pytest.param({"subscriptionType": None}, id="null plan"),
        pytest.param({"subscriptionType": "   "}, id="blank plan"),
        pytest.param({"subscriptionType": 20}, id="plan is not a string"),
        pytest.param("not-a-block", id="block is not an object"),
    ],
)
def test_claude_says_nothing_rather_than_guessing_a_plan(block: object) -> None:
    """Every way the store can decline to name a plan reads as *could not tell*.

    There is no "unknown" plan string on purpose: a client renders an absent
    subscription as absent, where a placeholder slug renders as a fact.
    """
    assert ClaudeQuotaProvider.parse_subscription(block) is None


def test_claude_leaves_the_sub_tier_absent_when_the_tier_does_not_name_the_plan() -> None:
    """The sub-tier is the tail after the plan's OWN name, never a slice on faith.

    Grove holds no table of Anthropic tier names, so a tier string it cannot
    anchor on the plan contributes nothing — an absent ``20x`` is a small loss
    where a wrong one is a claim about which plan is being paid for.
    """
    for tier_string in ("default_claude_max_20x", "enterprise_seat", None, 5):
        tier = ClaudeQuotaProvider.parse_subscription(
            {"subscriptionType": "pro", "rateLimitTier": tier_string}
        )
        assert tier is not None
        assert tier.plan == "pro"
        assert tier.detail is None


def test_claude_passes_an_unfamiliar_plan_through_unchanged() -> None:
    """A plan nobody here has seen is a plan somebody is paying for.

    Mapping it onto the nearest name Grove recognizes is precisely the confident
    wrong answer this field exists to refuse.
    """
    tier = ClaudeQuotaProvider.parse_subscription({"subscriptionType": "Max Ultra"})

    assert tier is not None
    assert (tier.plan, tier.label) == ("max ultra", "Max Ultra")


def test_claude_reports_the_plan_with_no_network_at_all(tmp_path: Path) -> None:
    """``describe`` is filesystem-only, and it already knows the plan.

    Which is the whole economy of this arm: the account list, the pre-collection
    state and every failed collection can name the subscription without the
    metered endpoint being asked anything.
    """
    root = _claude_profile(
        tmp_path,
        {
            "accessToken": "test-token",
            "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
        },
    )
    provider = ClaudeQuotaProvider(timeout=1.0, clock=_Clock(_NOW))

    view = provider.describe(QuotaAccount.mint(provider="claude_code", root=root))

    assert view.status == "unsupported"  # nothing collected yet
    assert view.subscription is not None
    assert view.subscription.plan == "max"


def test_claude_still_names_the_plan_while_the_endpoint_is_rate_limiting(
    tmp_path: Path,
) -> None:
    """The incident case: a refused probe must not blank a locally-known fact.

    The 2026-08-10 rate limit on this host left the quota page with no windows
    for hours. Which plan the account is on never depended on that endpoint, so
    withholding it exactly when the endpoint refuses would hide the one answer
    Grove could still give.
    """
    root = _claude_profile(
        tmp_path,
        {"accessToken": "test-token", "subscriptionType": "max", "rateLimitTier": "x_max_5x"},
    )
    clock = _Clock(_NOW)
    cfg = GroveConfig.model_validate(
        {"usage": {"quota": {"profiles": {"claude_code": [str(root)]}}}}
    )
    collector = QuotaCollector(
        cfg=cfg,
        clock=clock,
        transport=httpx.MockTransport(lambda _: httpx.Response(429, json={})),
        state_file=QuotaStateFile(tmp_path / "quota-state.json"),
    )

    view = collector.snapshot()[0]

    assert view.status == "rate_limited"
    assert view.windows == ()
    assert view.subscription is not None
    assert (view.subscription.plan, view.subscription.detail) == ("max", "5x")


def test_claude_carries_the_plan_beside_a_collected_reading(tmp_path: Path) -> None:
    """The ordinary path, end to end over a scripted transport.

    Driving the real provider rather than a double is what proves the plan
    survives the whole chain — credential store, view, ledger, render — since
    every hop here is one that rebuilds the view.
    """
    root = _claude_profile(
        tmp_path,
        {
            "accessToken": "test-token",
            "subscriptionType": "max",
            "rateLimitTier": "default_claude_max_20x",
        },
    )
    payload = {"limits": [{"kind": "session", "group": "session", "percent": 3.0}]}
    cfg = GroveConfig.model_validate(
        {"usage": {"quota": {"profiles": {"claude_code": [str(root)]}}}}
    )
    collector = QuotaCollector(
        cfg=cfg,
        clock=_Clock(_NOW),
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
        state_file=QuotaStateFile(tmp_path / "quota-state.json"),
    )

    view = collector.snapshot()[0]

    assert view.status == "ok"
    assert view.subscription is not None
    assert (view.subscription.plan, view.subscription.detail) == ("max", "20x")


# ─── Codex: the plan and the windows come out of the same block ──────────────


@pytest.mark.parametrize("plan", ["plus", "team", "prolite"])
def test_codex_reports_the_plan_the_server_named(tmp_path: Path, plan: str) -> None:
    """All three values observed across this host's 194 rollouts, verbatim."""
    root = _codex_profile(
        tmp_path,
        _codex_record(timestamp="2026-08-11T07:03:42.579Z", used_percent=25.0, plan_type=plan),
    )
    provider = CodexQuotaProvider(clock=_Clock(_NOW))

    view = provider.describe(QuotaAccount.mint(provider="codex", root=root))

    assert view.windows
    assert view.subscription is not None
    assert (view.subscription.plan, view.subscription.label) == (plan, plan)


def test_codex_reports_no_plan_when_the_reported_block_names_none(tmp_path: Path) -> None:
    """``plan_type`` is null on 47,764 of 53,286 real records, windows and all.

    A window with no plan beside it is the common case, not a parse failure.
    """
    root = _codex_profile(
        tmp_path,
        _codex_record(timestamp="2026-08-11T07:03:42.579Z", used_percent=25.0, plan_type=None),
    )
    provider = CodexQuotaProvider(clock=_Clock(_NOW))

    view = provider.describe(QuotaAccount.mint(provider="codex", root=root))

    assert view.windows
    assert view.subscription is None


def test_codex_never_borrows_a_plan_from_an_older_block(tmp_path: Path) -> None:
    """The plan is read from the block the WINDOWS came from, and only that one.

    This host's corpus runs ``plus`` → ``team`` → ``prolite`` across six months,
    so scanning back for the newest block that happens to name a plan would
    report a subscription the account left in March — with a current percentage
    beside it, which is what would make it believable.
    """
    root = _codex_profile(
        tmp_path,
        _codex_record(timestamp="2026-03-03T22:38:50.894Z", used_percent=11.0, plan_type="plus"),
        _codex_record(timestamp="2026-08-11T07:03:42.579Z", used_percent=25.0, plan_type=None),
    )
    provider = CodexQuotaProvider(clock=_Clock(_NOW))

    view = provider.describe(QuotaAccount.mint(provider="codex", root=root))

    assert [w.used_percent for w in view.windows] == [25.0]
    assert view.subscription is None


def test_codex_reports_no_plan_when_no_rollout_records_a_window(tmp_path: Path) -> None:
    root = _codex_profile(
        tmp_path,
        _codex_record(timestamp="2026-08-11T07:03:42.579Z", used_percent=None, plan_type="plus"),
    )
    provider = CodexQuotaProvider(clock=_Clock(_NOW))

    view = provider.describe(QuotaAccount.mint(provider="codex", root=root))

    assert view.status == "unsupported"
    assert view.subscription is None


# ─── the wire and the ledger ─────────────────────────────────────────────────


def test_an_account_view_has_no_subscription_until_something_reads_one() -> None:
    """Absent is the default, so no client can mistake a silence for a plan."""
    assert BillingAccountView(account_id="a", provider="codex", label="a").subscription is None


def test_the_plan_survives_the_durable_ledger(tmp_path: Path) -> None:
    """A reading is served from disk after a restart, and it keeps its plan.

    The ledger holds no credential, and a plan name is not one — it is the same
    fact the wire already carries, which is what makes persisting it admissible.
    """
    ledger = QuotaStateFile(tmp_path / "quota-state.json")
    view = BillingAccountView(
        account_id="codex-1",
        provider="codex",
        label="codex",
        billing_mode="subscription",
        subscription={"plan": "team", "label": "team"},  # type: ignore[arg-type]
        status="ok",
    )
    ledger.merge({"codex-1": QuotaProbeState(last_view=view, fetched_at=_NOW)})

    restored = QuotaStateFile(tmp_path / "quota-state.json").load()["codex-1"]
    rendered = restored.render(_NOW + timedelta(seconds=5))

    assert rendered is not None
    assert rendered.subscription is not None
    assert rendered.subscription.plan == "team"
