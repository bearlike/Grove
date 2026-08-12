"""Claude Code quota — the profile's own OAuth credential, read at request time.

Verified live against a real subscription login on **2026-08-09** (Claude Code
2.1.x credential store, ``GET https://api.anthropic.com/api/oauth/usage`` with
the ``oauth-2025-04-20`` beta header, HTTP 200).

Three decisions carry the module:

**The endpoint host is a constant, and that is the security property.** An
OAuth subscription token is issued by Anthropic and is meaningful nowhere else,
so the destination is derived from the credential's issuer rather than from
config. A profile that points the *agent* at a gateway (``ANTHROPIC_BASE_URL``)
must not thereby cause Grove to send that profile's personal credential to the
gateway; making the host configurable would be exactly that bug with a knob on
it.

**The response's ``limits`` array is the seam, not the top-level pool keys.**
The same payload carries a dozen named top-level pools, most of them ``null``
and several under rotating internal codenames. ``limits`` is the provider's own
normalized list — ``{kind, group, percent, resets_at, scope}`` — so parsing it
means a pool Anthropic adds tomorrow renders as a real window instead of being
silently dropped by a hard-coded key list.

**A refusal's ``Retry-After`` is the one header read, and it is read because the
alternative is guessing.** Reading quota is itself a metered act, so when this
endpoint says "not yet" the only correct response is to believe it — a client
that invents its own interval is arguing with a rate limiter. Both forms RFC
9110 allows are parsed, since handling only delta-seconds silently ignores every
server that sends a date, which looks exactly like a server that said nothing.
Measured on **2026-08-10**, a 429 here carries ``retry-after: 0`` — present and
meaningless — so the collector takes the longer of this hint and its own
schedule rather than obeying it outright.

**The PLAN is local evidence and costs nothing.** The same credential block
names the subscription (``subscriptionType``) and its rate-limit tier
(``rateLimitTier``), so which plan an account is on is answered by the file this
provider already opens — no request, and an answer even while the endpoint is
refusing to give one.

**The endpoint reports no window DURATION.** ``window_seconds`` is therefore
``None`` on every window here, which is honest: the durations are famously
"5 hours" and "7 days" but the payload does not say so, and writing them in
would be Grove asserting a boundary the provider has already moved once.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

import httpx
from loguru import logger

from grove.core.contracts.usage import (
    BillingAccountView,
    QuotaScope,
    SubscriptionTier,
    SubscriptionWindowView,
    UsageProvider,
)
from grove.core.usage.quota.base import (
    QuotaAccount,
    QuotaProvider,
    as_float,
    parse_epoch,
    parse_iso_datetime,
    parse_retry_after,
    subscription_tier,
)


@dataclass(frozen=True, slots=True)
class _OAuthCredential:
    """The one thing read out of a credential store, held only for this call.

    Deliberately not logged, not returned to a caller, and not stored anywhere:
    the instance dies with the request that built it.

    ``subscription`` is the exception that proves the rule — it is a plan name
    sitting in the same file, carries nothing secret, and is the only member
    copied onto a view. Reading it here rather than anywhere else is what makes
    "which plan is this account on" cost zero requests.
    """

    token: str
    expires_at: datetime | None
    subscription: SubscriptionTier | None = None


class ClaudeQuotaProvider(QuotaProvider):
    """Subscription windows for one ``CLAUDE_CONFIG_DIR`` profile root."""

    provider: ClassVar[UsageProvider] = "claude_code"
    CREDENTIALS_FILE: ClassVar[str] = ".credentials.json"
    """Where the CLI keeps its login, relative to a profile root (mode 0600)."""

    OAUTH_KEY: ClassVar[str] = "claudeAiOauth"
    """The subscription login inside that file. A store holding only other keys
    (an MCP server's OAuth grant, say) is a store with no subscription."""

    USAGE_URL: ClassVar[str] = "https://api.anthropic.com/api/oauth/usage"
    OAUTH_BETA: ClassVar[str] = "oauth-2025-04-20"

    def __init__(
        self,
        *,
        timeout: float,
        clock: Callable[[], datetime],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._timeout = timeout
        self._clock = clock
        self._transport = transport
        self._http: httpx.Client | None = None

    # ─── the two answers ────────────────────────────────────────────────────

    def describe(self, account: QuotaAccount) -> BillingAccountView:
        """What the credential store alone can say. No network.

        Three of the four outcomes are decided here, which is why this is worth
        having: a root with no login and a root with an expired one both need a
        human, and neither should cost a request to discover.
        """
        credential = self._read_credential(account.root)
        if credential is None:
            return self.failure(
                account,
                status="unsupported",
                detail="this profile root holds no Claude subscription login",
            )
        if self._expired(credential):
            return self.failure(
                account,
                status="auth_expired",
                detail="the profile's login has expired — sign in again with the tool itself",
                billing_mode="subscription",
                subscription=credential.subscription,
            )
        return self.failure(
            account,
            status="unsupported",
            detail="no quota snapshot collected for this account yet",
            billing_mode="subscription",
            subscription=credential.subscription,
        )

    def collect(self, account: QuotaAccount) -> BillingAccountView:
        """Read the profile's credential and ask Anthropic what remains."""
        local = self.describe(account)
        if local.status != "unsupported" or local.billing_mode != "subscription":
            # Either the store answered definitively (no login, expired, API key)
            # or there is nothing to send. Only the "not collected yet" shape
            # continues past here.
            return local
        credential = self._read_credential(account.root)
        if credential is None:  # pragma: no cover - describe() already proved it exists
            return local

        status, payload, retry_after = self._fetch(credential.token)
        if status == 200 and payload is not None:
            observed_at = self._clock()
            windows = self.parse_windows(payload, observed_at=observed_at)
            if not windows:
                return self.failure(
                    account,
                    status="unsupported",
                    detail="the usage endpoint reported no rate-limit windows for this account",
                    billing_mode="subscription",
                    subscription=credential.subscription,
                )
            return BillingAccountView(
                account_id=account.account_id,
                provider=account.provider,
                label=account.label,
                billing_mode="subscription",
                subscription=credential.subscription,
                status="ok",
                windows=windows,
                observed_at=observed_at,
            )
        return self.failure(
            account,
            **self._failure_kind(status),
            billing_mode="subscription",
            retry_after=retry_after,
            subscription=credential.subscription,
        )

    # ─── pure normalization ─────────────────────────────────────────────────

    @classmethod
    def parse_windows(
        cls, payload: object, *, observed_at: datetime
    ) -> tuple[SubscriptionWindowView, ...]:
        """``limits[]`` → windows. Pure, total, and tolerant of a widened shape.

        An entry missing a percentage is dropped rather than rendered as zero:
        an unmeasured window and an unused one are different facts, and only one
        of them is good news.
        """
        if not isinstance(payload, dict):
            return ()
        entries = payload.get("limits")
        if not isinstance(entries, list):
            return ()
        windows: list[SubscriptionWindowView] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            used = as_float(entry.get("percent"))
            if used is None:
                continue
            kind = entry.get("kind")
            windows.append(
                SubscriptionWindowView(
                    scope=cls._scope(entry),
                    label=str(kind) if kind else "limit",
                    window_seconds=None,
                    used_percent=used,
                    remaining_percent=cls.remaining_percent(used),
                    resets_at=parse_iso_datetime(entry.get("resets_at")),
                    observed_at=observed_at,
                    evidence="provider_endpoint",
                )
            )
        return tuple(windows)

    @classmethod
    def parse_subscription(cls, block: object) -> SubscriptionTier | None:
        """The plan named inside a ``claudeAiOauth`` block. Pure and total.

        Verified on this host on **2026-08-11** against Claude Code 2.1.227: the
        block carries ``subscriptionType`` (``"max"``) beside
        ``rateLimitTier`` (``"default_claude_max_20x"``), and the same pair is
        mirrored into ``~/.claude.json``'s ``oauthAccount``. **The endpoint is
        deliberately not consulted for this** — reading it is a metered act that
        has already rate-limited this host once, and the answer is sitting in a
        file the provider opens anyway.

        The sub-tier is whatever follows the plan's own name inside the
        rate-limit tier, so ``max`` + ``default_claude_max_20x`` yields ``20x``
        with no table of Anthropic tier names in Grove. A tier string that does
        not contain the plan is left alone rather than sliced on a guess: an
        empty sub-tier is a small loss, a wrong one is a claim about a bill.
        """
        if not isinstance(block, dict):
            return None
        tier = subscription_tier(block.get("subscriptionType"))
        if tier is None or tier.plan is None:
            return None
        detail = cls._sub_tier(block.get("rateLimitTier"), tier.plan)
        return tier.model_copy(update={"detail": detail})

    @staticmethod
    def _sub_tier(rate_limit_tier: object, plan: str) -> str | None:
        """The part of ``rateLimitTier`` that follows the plan name, if any."""
        if not isinstance(rate_limit_tier, str):
            return None
        _, separator, tail = rate_limit_tier.lower().partition(f"_{plan.lower()}_")
        return (tail.strip("_") or None) if separator else None

    @staticmethod
    def _scope(entry: dict[str, Any]) -> QuotaScope:
        """Map one ``limits[]`` entry onto the neutral scope vocabulary.

        ``scope`` wins over ``group``: the provider setting it is the provider
        saying "this window governs one slice", which is what ``model`` means
        here. Anything unrecognized is ``other`` rather than forced into the
        nearest bucket — a new group renders as a real window with an honest
        grouping instead of being mislabelled as a weekly budget.
        """
        if entry.get("scope"):
            return "model"
        group = entry.get("group")
        if group == "session":
            return "session"
        if group == "weekly":
            return "weekly"
        return "other"

    @staticmethod
    def _failure_kind(status: int) -> dict[str, Any]:
        """HTTP status → the distinct outcome an operator can act on.

        Never the response body: a 4xx body from an auth endpoint is exactly
        where a token echo would live.
        """
        if status in (401, 403):
            return {
                "status": "auth_expired",
                "detail": "the profile's login was rejected — sign in again with the tool itself",
            }
        if status == 429:
            return {
                "status": "rate_limited",
                "detail": "the usage endpoint is rate-limiting this account; retrying later",
            }
        if status == 0:
            return {
                "status": "unreachable",
                "detail": "the usage endpoint could not be reached",
            }
        return {
            "status": "unreachable",
            "detail": f"the usage endpoint answered HTTP {status}",
        }

    # ─── edges ──────────────────────────────────────────────────────────────

    def _read_credential(self, root: Path) -> _OAuthCredential | None:
        """The profile's own store, opened read-only at this instant.

        Never copied, never rewritten, never refreshed — a token this returns
        lives only as long as the request that asked for it. Any read problem
        (absent, unreadable, malformed) is the same answer: there is no usable
        subscription login here.
        """
        path = root / self.CREDENTIALS_FILE
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            logger.debug("claude credential store at {} is not valid JSON", path.name)
            return None
        block = data.get(self.OAUTH_KEY) if isinstance(data, dict) else None
        if not isinstance(block, dict):
            return None
        token = block.get("accessToken")
        if not isinstance(token, str) or not token:
            return None
        expires_at = parse_epoch(block.get("expiresAt"), unit_ms=True)
        return _OAuthCredential(
            token=token,
            expires_at=expires_at,
            subscription=self.parse_subscription(block),
        )

    def _expired(self, credential: _OAuthCredential) -> bool:
        """Has the access token's own stated expiry passed?

        Checked before sending so an expired login costs no request and reports
        the reason a 401 would have implied anyway. Grove never refreshes it:
        rewriting somebody else's credential store from a read-only audit page
        is a side effect nobody asked for, and a racing refresh corrupts it.
        """
        return credential.expires_at is not None and credential.expires_at <= self._clock()

    def _fetch(self, token: str) -> tuple[int, object | None, datetime | None]:
        """One bounded GET. ``(0, None, None)`` means the request never landed.

        The token is composed into the header here and nowhere else, and no
        branch of this method puts a header, a body or the token into a log.

        The third member is the endpoint's own ``Retry-After``, translated to an
        instant. It is the ONE header read out of a refusal, and it is read
        precisely because the alternative is Grove choosing for itself how soon
        to knock again at a door that just said "not yet".
        """
        try:
            response = self._client().get(
                self.USAGE_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "anthropic-beta": self.OAUTH_BETA,
                    "accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            logger.debug("claude usage endpoint unreachable: {}", type(exc).__name__)
            return 0, None, None
        if response.status_code != 200:
            retry_after = parse_retry_after(response.headers.get("retry-after"), self._clock())
            return response.status_code, None, retry_after
        try:
            return 200, response.json(), None
        except ValueError:
            return 0, None, None

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout, transport=self._transport)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None


__all__ = ["ClaudeQuotaProvider"]
