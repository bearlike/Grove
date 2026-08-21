"""Quota gateway source: one envelope, vendor-labelled accounts, durable fallback."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from grove.core.config import GroveConfig
from grove.core.usage.quota import GatewayQuotaProvider, QuotaCollector
from grove.core.usage.quota._state import QuotaStateFile
from grove.core.usage.quota.base import subscription_tier
from grove.core.usage.quota.gateway import _GatewayEnvelope


def _payload(*, stale: bool = False, age_seconds: int = 192) -> dict[str, object]:
    return {
        "generated_at": 1786740785,
        "ok": True,
        "binding": {
            "subscription": "acct1",
            "provider": "anthropic",
            "window": "7d",
            "used_percent": 100.0,
            "resets_at": 1786791600,
        },
        "age_seconds": age_seconds,
        "stale": stale,
        "refresh_interval_seconds": 300,
        "subscriptions": [
            {
                "id": "acct1",
                "provider": "anthropic",
                "email": "first@example.com",
                "plan": None,
                "source": "ratelimit-headers",
                "ok": True,
                "error": None,
                "windows": [
                    {
                        "key": "five_hour",
                        "label": "5h",
                        "used_percent": 0.0,
                        "resets_at": 1786758600,
                        "status": "allowed",
                    },
                    {
                        "key": "seven_day",
                        "label": "7d",
                        "used_percent": 100.0,
                        "resets_at": 1786791600,
                        "status": "rejected",
                    },
                ],
                "overage": {"status": "rejected", "disabled_reason": "org_level_disabled"},
                "representative_claim": "seven_day",
            },
            {
                "id": "codex",
                "provider": "openai-codex",
                "email": "second@example.com",
                "plan": "prolite",
                "source": "codex-usage-endpoint",
                "ok": True,
                "error": None,
                "windows": [
                    {
                        "key": "primary",
                        "label": "7d",
                        "used_percent": 11.0,
                        "resets_at": 1787196904,
                        "status": None,
                    },
                    {
                        "key": "codex_bengalfox",
                        "label": "7d",
                        "used_percent": 0.0,
                        "resets_at": 1787345585,
                        "status": None,
                        "limit_name": "GPT-5.3-Codex-Spark",
                    },
                ],
                "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
            },
        ],
    }


def _cfg() -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "usage": {
                "quota": {
                    "gateway": {
                        "base_url": "https://quota.example.test/snapshot",
                        "token_env": "QUOTA_GATEWAY_TOKEN",
                    },
                    "labels": {"claude_code-gateway:acct1": "Primary"},
                }
            }
        }
    )


def _provider(
    *,
    clock: datetime,
    transport: httpx.BaseTransport,
) -> GatewayQuotaProvider:
    return GatewayQuotaProvider(
        base_url="https://quota.example.test/snapshot",
        token_env="QUOTA_GATEWAY_TOKEN",
        timeout=10,
        clock=lambda: clock,
        transport=transport,
        env={"QUOTA_GATEWAY_TOKEN": "gateway-token"},
    )


def test_gateway_reads_one_envelope_and_keeps_each_subscription_vendor_labelled() -> None:
    requests: list[httpx.Request] = []
    now = datetime(2026, 8, 14, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_payload())

    collector = QuotaCollector(
        cfg=_cfg(),
        clock=lambda: now,
        providers=(_provider(clock=now, transport=httpx.MockTransport(handler)),),
    )

    accounts = collector.snapshot()

    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer gateway-token"
    claude, codex = accounts
    assert (claude.account_id, claude.provider, claude.label) == (
        "claude_code-gateway:acct1",
        "claude_code",
        "Primary",
    )
    assert (codex.account_id, codex.provider, codex.subscription.plan) == (
        "codex-gateway:codex",
        "codex",
        "prolite",
    )
    assert [
        (window.label, window.scope, window.remaining_percent) for window in claude.windows
    ] == [("5h", "session", 100.0), ("7d", "weekly", 0.0)]
    assert claude.windows[1].resets_at == datetime.fromtimestamp(1786791600, tz=UTC)
    assert claude.observed_at == now - timedelta(seconds=192)


def test_gateway_keeps_a_failing_subscription_separate_from_healthy_ones() -> None:
    payload = _payload()
    subscriptions = payload["subscriptions"]
    assert isinstance(subscriptions, list)
    subscriptions[1]["ok"] = False
    subscriptions[1]["error"] = "upstream Codex quota is unavailable"
    now = datetime(2026, 8, 14, tzinfo=UTC)

    collector = QuotaCollector(
        cfg=_cfg(),
        clock=lambda: now,
        providers=(
            _provider(
                clock=now,
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)),
            ),
        ),
    )

    healthy, failing = collector.snapshot()

    assert healthy.status == "ok"
    assert failing.status == "unreachable"
    assert failing.detail == "upstream Codex quota is unavailable"


def test_an_unreachable_gateway_serves_ledger_last_good_without_dropping_accounts(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 14, tzinfo=UTC)
    ledger = QuotaStateFile(tmp_path / "quota-state.json")
    first = QuotaCollector(
        cfg=_cfg(),
        clock=lambda: now,
        providers=(
            _provider(
                clock=now,
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_payload())),
            ),
        ),
        state_file=ledger,
    )
    assert len(first.snapshot()) == 2

    restarted = QuotaCollector(
        cfg=_cfg(),
        clock=lambda: now + timedelta(minutes=5),
        providers=(
            _provider(
                clock=now + timedelta(minutes=5),
                transport=httpx.MockTransport(
                    lambda _: (_ for _ in ()).throw(httpx.ConnectError("down"))
                ),
            ),
        ),
        state_file=ledger,
    )

    accounts = restarted.snapshot()

    assert [(account.account_id, account.status) for account in accounts] == [
        ("claude_code-gateway:acct1", "stale"),
        ("codex-gateway:codex", "stale"),
    ]
    assert [window.used_percent for window in accounts[0].windows] == [0.0, 100.0]


def test_an_unknown_field_from_a_live_gateway_does_not_blank_every_account() -> None:
    """The shape a REAL gateway sent, which the issue's example payload lacked.

    Measured against the live endpoint on 2026-08-14: it carries a top-level
    ``error`` (null when healthy) that this model never declared, and under the
    ``extra="forbid"`` it shipped with, that one key rejected the whole envelope
    — ``_fetch`` swallowed the ValidationError and every subscription rendered
    unreachable, with nothing on the host naming the cause.

    So the envelope tolerates additions. The values here are fictional; only the
    SHAPE is the captured fact.
    """
    payload = {
        "generated_at": 1786800000,
        "ok": True,
        "error": None,
        "age_seconds": 181,
        "stale": False,
        "refresh_interval_seconds": 300,
        "binding": {
            "subscription": "sub-a",
            "provider": "anthropic",
            "window": "weekly",
            "used_percent": 41.0,
            "resets_at": 1787000000,
        },
        "subscriptions": [
            {
                "id": "sub-a",
                "provider": "anthropic",
                "email": "someone@example.invalid",
                "plan": None,
                "source": "oauth",
                "ok": True,
                "error": None,
                "overage": None,
                "representative_claim": None,
                "windows": [
                    {
                        "key": "5h",
                        "label": "5h",
                        "used_percent": 12.0,
                        "resets_at": 1786820000,
                        "status": "ok",
                    },
                ],
            },
        ],
        # A field nobody has invented yet. Tolerating it is the whole point.
        "some_future_field": {"unknown": True},
    }

    envelope = _GatewayEnvelope.model_validate(payload)

    assert len(envelope.subscriptions) == 1
    assert envelope.age_seconds == 181
    assert envelope.error is None


def test_an_unknown_vendor_from_a_live_gateway_does_not_blank_every_account() -> None:
    """The same outage as the unknown-field case above, one axis over.

    Measured against the live endpoint on 2026-08-16: it grew a fourth
    subscription (Alibaba Model Studio Token Plan) whose ``provider`` was not in
    the closed ``Literal`` this model shipped with, and that ONE unrecognised
    string failed the whole envelope — the two Anthropic accounts and Codex, all
    healthy, went back to rendering as unreachable.

    So a vendor Grove has not met maps to ``generic`` rather than invalidating
    the payload, and the accounts it already understands keep their own provider.
    """
    payload = {
        "generated_at": 1786800000,
        "ok": True,
        "error": None,
        "age_seconds": 12,
        "stale": False,
        "refresh_interval_seconds": 300,
        "binding": {
            "subscription": "alibaba",
            "provider": "alibaba-token-plan",
            "window": "7d",
            "used_percent": 76.0,
            "resets_at": 1787205660,
        },
        "subscriptions": [
            {
                "id": "acct1",
                "provider": "anthropic",
                "ok": True,
                "windows": [
                    {"key": "five_hour", "label": "5h", "used_percent": 12.0},
                ],
            },
            {
                "id": "alibaba",
                "provider": "alibaba-token-plan",
                "plan": "token-plan-individual",
                "ok": True,
                "windows": [
                    {"key": "seven_day", "label": "7d", "used_percent": 76.0},
                ],
            },
        ],
    }

    envelope = _GatewayEnvelope.model_validate(payload)
    providers = tuple(
        GatewayQuotaProvider._provider_for(subscription) for subscription in envelope.subscriptions
    )

    assert len(envelope.subscriptions) == 2
    assert providers == ("claude_code", "generic")
    assert envelope.binding is not None
    assert envelope.binding.used_percent == 76.0


def test_a_gateway_subscription_keeps_its_sub_tier_beside_its_plan() -> None:
    """``max 20x`` survives the gateway, which ``max`` alone does not express.

    The local ``ClaudeQuotaProvider`` splits Anthropic's plan from its
    rate-limit sub-tier and reports both, so an operator can tell a Max 20x
    account from a Max 5x one. Routing the same subscriptions through the
    gateway dropped the second half twice over: the envelope had nowhere to
    carry it, and ``_view`` called ``subscription_tier`` without a ``detail``.
    Two accounts on visibly different plans then rendered identically.
    """
    payload = {
        "generated_at": 1786800000,
        "ok": True,
        "age_seconds": 3,
        "stale": False,
        "subscriptions": [
            {
                "id": "acct1",
                "provider": "anthropic",
                "plan": "max",
                "plan_detail": "20x",
                "ok": True,
                "windows": [{"key": "5h", "label": "5h", "used_percent": 12.0}],
            },
            {
                "id": "acct2",
                "provider": "anthropic",
                "plan": "max",
                "plan_detail": "5x",
                "ok": True,
                "windows": [{"key": "5h", "label": "5h", "used_percent": 70.0}],
            },
            {
                # A gateway that never learned the field still reports its plan.
                "id": "codex",
                "provider": "openai-codex",
                "plan": "prolite",
                "ok": True,
                "windows": [{"key": "7d", "label": "7d", "used_percent": 85.0}],
            },
        ],
    }

    envelope = _GatewayEnvelope.model_validate(payload)
    tiers = tuple(
        subscription_tier(subscription.plan, detail=subscription.plan_detail)
        for subscription in envelope.subscriptions
    )

    assert [(tier.plan, tier.detail) for tier in tiers if tier is not None] == [
        ("max", "20x"),
        ("max", "5x"),
        ("prolite", None),
    ]
