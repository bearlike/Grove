"""Quota gateway — one unmetered snapshot covering subscriptions from many vendors.

The gateway is an aggregate boundary, not a new ``UsageProvider``: each
subscription names its existing vendor and becomes an account under that
vendor's account id. It serves its own background snapshot, so reading it is
unmetered from Grove's perspective. Its ``age_seconds`` and ``stale`` fields
are evidence from that background schedule, not something Grove estimates from
its own clock.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import ClassVar

import httpx
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grove.core.contracts.usage import BillingAccountView, SubscriptionWindowView, UsageProvider
from grove.core.usage.quota.base import QuotaAccount, QuotaProvider, parse_epoch, subscription_tier

# An UNKNOWN FIELD IS TOLERATED HERE, deliberately unlike every config model in
# this tree — and the first contact with a real gateway is what settled it. The
# live endpoint sends a top-level `error` this model had not declared (its
# absence from the issue's example payload is the whole reason), and under
# `extra="forbid"` that one key rejected the entire envelope: `_fetch` caught the
# ValidationError, returned None, and the usage page rendered every subscription
# as unreachable. A strict config model fails loudly at load; a strict READER of
# somebody else's evolving API fails silently at the far end of a `try`, and the
# operator sees a blank page with nothing naming the cause.
#
# Tolerating an addition costs nothing, because the fields Grove actually reads
# are declared and REQUIRED — a wrong endpoint that happens to return JSON still
# fails on the missing ones, so this does not trade the misconfiguration check
# away. The devcontainer boundary in `core/CLAUDE.md` reached the same verdict
# from the other direction: tolerate when what is unknown is a SHAPE.
_EXTERNAL = ConfigDict(extra="ignore")

# AN UNKNOWN VENDOR IS TOLERATED FOR THE SAME REASON AN UNKNOWN FIELD IS, and it
# took the same outage to learn it twice. `provider` was a closed
# `Literal["anthropic", "openai-codex"]`, so the day the gateway grew a fourth
# subscription (Alibaba Model Studio) that one unrecognised string failed the
# whole envelope -- not the new account, EVERY account -- and the usage page went
# back to rendering four unreachable cards. A reader of somebody else's evolving
# API cannot treat "a vendor I have not met" as "this payload is malformed".
#
# Mapping instead of enumerating: a known vendor lands on the Grove provider that
# already owns its accounts, and anything else becomes `generic`, which is the
# member that exists precisely for a subscription Grove has no agent integration
# for. `UsageProvider` is deliberately NOT extended here -- it mirrors
# `config.AgentKind` under the webapp codegen drift-check and means "the agent
# tool a session ran under", which a billing vendor is not.
_VENDOR_PROVIDERS: dict[str, UsageProvider] = {
    "anthropic": "claude_code",
    "openai-codex": "codex",
}
_GATEWAY_PREFIXES: tuple[str, ...] = tuple(
    f"{provider}-gateway:" for provider in ("claude_code", "codex", "generic")
)


class _GatewayWindow(BaseModel):
    """The quota fields for one gateway-reported rate-limit window."""

    model_config = _EXTERNAL

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    used_percent: float
    resets_at: int | float | None = None
    status: str | None = None
    limit_name: str | None = None


class _GatewaySubscription(BaseModel):
    """One account in the gateway's aggregate envelope."""

    model_config = _EXTERNAL

    id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    email: str | None = None
    plan: str | None = None
    plan_detail: str | None = None
    """The sub-tier beside the plan, as ``20x`` beside ``max``.

    Carried as its own field rather than folded into ``plan`` for the reason
    ``ClaudeQuotaProvider`` splits it in the first place: ``max`` and ``20x``
    answer different questions, and a reader handed ``"max 20x"`` cannot recover
    the split without a table of Anthropic's tier names — which is exactly the
    table `subscription_tier` refuses to keep. A gateway that omits it loses
    only the detail, never the plan.
    """
    source: str | None = None
    ok: bool
    error: str | None = None
    windows: tuple[_GatewayWindow, ...] = ()
    overage: dict[str, str | bool | None] | None = None
    representative_claim: str | None = None
    credits: dict[str, str | bool | None] | None = None


class _GatewayBinding(BaseModel):
    """The gateway's aggregate worst-window summary."""

    model_config = _EXTERNAL

    subscription: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    window: str = Field(min_length=1)
    used_percent: float
    resets_at: int | float | None = None


class _GatewayEnvelope(BaseModel):
    """The validated external quota-gateway response."""

    model_config = _EXTERNAL

    generated_at: int | float
    ok: bool
    error: str | None = None
    """The gateway's own aggregate failure, distinct from a per-subscription one."""
    binding: _GatewayBinding | None = None
    age_seconds: int = Field(ge=0)
    stale: bool
    refresh_interval_seconds: int | None = Field(default=None, ge=0)
    subscriptions: tuple[_GatewaySubscription, ...]


class GatewayQuotaProvider(QuotaProvider):
    """Every subscription reported by one configured quota gateway endpoint."""

    provider: ClassVar[UsageProvider] = "generic"
    metered: ClassVar[bool] = False
    enumerates_accounts: ClassVar[bool] = True

    def __init__(
        self,
        *,
        base_url: str,
        token_env: str,
        timeout: float,
        clock: Callable[[], datetime],
        transport: httpx.BaseTransport | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._base_url = base_url
        self._token_env = token_env
        self._timeout = timeout
        self._clock = clock
        self._transport = transport
        self._env = env if env is not None else os.environ
        self._http: httpx.Client | None = None
        self._subscriptions: dict[str, _GatewaySubscription] = {}
        self._envelope: _GatewayEnvelope | None = None

    def accounts(
        self, *, labels: Mapping[str, str], known_account_ids: tuple[str, ...]
    ) -> tuple[QuotaAccount, ...]:
        """Fetch and enumerate one account for every subscription in the snapshot.

        A failed roster fetch still returns ledger-known gateway accounts, so the
        collector reaches its existing last-known-good fallback instead of
        silently dropping them from the page.
        """
        envelope = self._fetch()
        if envelope is None:
            return tuple(
                QuotaAccount.from_handle(
                    provider=self._provider_for_account_id(account_id),
                    handle=self._handle_from_account_id(account_id),
                    label=(labels.get(account_id) or self._handle_from_account_id(account_id)),
                    labels=labels,
                )
                for account_id in known_account_ids
                if self._is_gateway_account_id(account_id)
            )
        self._envelope = envelope
        self._subscriptions = {
            subscription.id: subscription for subscription in envelope.subscriptions
        }
        return tuple(
            QuotaAccount.from_handle(
                provider=self._provider_for(subscription),
                handle=subscription.id,
                label=subscription.email or subscription.id,
                labels=labels,
            )
            for subscription in envelope.subscriptions
        )

    def describe(self, account: QuotaAccount) -> BillingAccountView:
        """Gateway evidence already read when its aggregate accounts were enumerated."""
        return self._view(account, self._subscriptions.get(self._handle(account)))

    def collect(self, account: QuotaAccount) -> BillingAccountView:
        """Use the one envelope read for this collector pass, never one GET per account."""
        return self.describe(account)

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def _fetch(self) -> _GatewayEnvelope | None:
        token = self._env.get(self._token_env, "")
        if not self._base_url or not token:
            return None
        try:
            response = self._client().get(
                self._base_url,
                headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
            )
            response.raise_for_status()
            return _GatewayEnvelope.model_validate(response.json())
        except httpx.HTTPError as exc:
            # Transient and self-clearing: the ordinary "endpoint is down" case,
            # already visible as last-known-good on the page. DEBUG is right.
            logger.debug("quota gateway unavailable: {}", type(exc).__name__)
            return None
        except (ValidationError, ValueError) as exc:
            # NOT transient: the gateway answered and Grove could not read it, so
            # every account silently renders unreachable until someone changes
            # code. That is the failure this provider actually shipped with, and
            # at DEBUG — Grove's default sink is WARNING — nothing on the host
            # said a word. A contract that moved has to name itself.
            logger.warning(
                "quota gateway at {} answered with a payload Grove cannot read ({}); "
                "reporting its accounts as unreachable",
                self._base_url,
                type(exc).__name__,
            )
            return None

    def _view(
        self, account: QuotaAccount, subscription: _GatewaySubscription | None
    ) -> BillingAccountView:
        if subscription is None or self._envelope is None:
            return self.failure(
                account,
                status="unreachable",
                detail="the quota gateway could not be reached",
            )
        if not subscription.ok:
            return self.failure(
                account,
                status="unreachable",
                detail=(
                    subscription.error or "the quota gateway could not collect this subscription"
                ),
                billing_mode="subscription",
                subscription=subscription_tier(subscription.plan, detail=subscription.plan_detail),
            )
        observed_at = self._clock() - timedelta(seconds=self._envelope.age_seconds)
        labels = self._window_labels(subscription.windows)
        windows = tuple(
            SubscriptionWindowView(
                scope=self.scope_for_window(self._window_seconds(window.label)),
                # CARRY THE DURATION, not just the scope it implies. Burn rate is
                # `used_percent` against the fraction of the window elapsed, and
                # elapsed needs a START — which is `resets_at - window_seconds`.
                # Without it every window is honestly "unknown" and the capacity
                # and pace signals are blank for every subscription, which is
                # exactly what a gateway-only host saw. The label is the only
                # duration the envelope carries, so parsing it here is what makes
                # the projection possible at all.
                window_seconds=self._window_seconds(window.label),
                label=labels[index],
                used_percent=window.used_percent,
                remaining_percent=self.remaining_percent(window.used_percent),
                resets_at=parse_epoch(window.resets_at),
                observed_at=observed_at,
                evidence="provider_endpoint",
            )
            for index, window in enumerate(subscription.windows)
        )
        if not windows:
            return self.failure(
                account,
                status="unsupported",
                detail="the quota gateway reported no rate-limit windows for this subscription",
                billing_mode="subscription",
                subscription=subscription_tier(subscription.plan, detail=subscription.plan_detail),
            )
        stale = self._envelope.stale
        return BillingAccountView(
            account_id=account.account_id,
            provider=account.provider,
            label=account.label,
            billing_mode="subscription",
            subscription=subscription_tier(subscription.plan, detail=subscription.plan_detail),
            status="stale" if stale else "ok",
            detail="the quota gateway snapshot is stale" if stale else None,
            windows=windows,
            observed_at=observed_at,
            stale_seconds=self._envelope.age_seconds if stale else None,
        )

    @staticmethod
    def _window_labels(windows: tuple[_GatewayWindow, ...]) -> list[str]:
        """Per-window display labels, unique within the subscription.

        A LABEL IS NOT AN IDENTITY HERE, and a real gateway proves it: one Codex
        account reports two separate `7d` windows, distinguished only by their
        `key` (`primary` against a named one). `quota_snapshots` is
        `UNIQUE(account_id, scope, label)`, so passing the bare label through
        made the second window collide with the first and the whole
        `/usage/quotas` request died on an IntegrityError — a 500 for every
        account, not just the ambiguous one.

        Qualifying only where the gateway is genuinely ambiguous keeps `5h`/`7d`
        exactly as they read today; a window whose duration already names it
        uniquely is left alone. The `key` is the gateway's own discriminator, so
        this invents no vocabulary of Grove's.
        """
        seen: dict[str, int] = {}
        for window in windows:
            seen[window.label] = seen.get(window.label, 0) + 1
        return [
            f"{window.label} ({window.key})" if seen[window.label] > 1 else window.label
            for window in windows
        ]

    @staticmethod
    def _provider_for(subscription: _GatewaySubscription) -> UsageProvider:
        return _VENDOR_PROVIDERS.get(subscription.provider, "generic")

    @staticmethod
    def _handle(account: QuotaAccount) -> str:
        return GatewayQuotaProvider._handle_from_account_id(account.account_id)

    @staticmethod
    def _is_gateway_account_id(account_id: str) -> bool:
        return account_id.startswith(_GATEWAY_PREFIXES)

    @staticmethod
    def _handle_from_account_id(account_id: str) -> str:
        _, _, handle = account_id.partition("gateway:")
        return handle

    @staticmethod
    def _provider_for_account_id(account_id: str) -> UsageProvider:
        # Mirrors how `QuotaAccount.from_handle` composed the id, so the ledger
        # fallback re-groups an account under the same provider it was stored
        # with even while the gateway is unreachable and cannot say.
        for provider in ("claude_code", "codex"):
            if account_id.startswith(f"{provider}-"):
                return provider
        return "generic"

    @staticmethod
    def _window_seconds(label: str) -> int | None:
        suffix = label.lower().strip()
        if suffix.endswith("h") and suffix[:-1].isdigit():
            return int(suffix[:-1]) * 3600
        if suffix.endswith("d") and suffix[:-1].isdigit():
            return int(suffix[:-1]) * 86_400
        return None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout, transport=self._transport)
        return self._http


__all__ = ["GatewayQuotaProvider"]
