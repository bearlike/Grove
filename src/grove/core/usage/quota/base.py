"""The quota-provider seam: what an account IS, and what a provider must answer.

Distinct from ``AgentAdapter`` on purpose. A transcript adapter answers *what
happened* by reading files a tool already wrote; a quota provider answers *what
remains* by reading a tool's own credential store and the endpoint that
credential selects. The two have different failure modes, different privacy
rules and different refresh economics, so they are different seams.

Three invariants live here rather than in either implementation:

* **A provider is not an account.** :class:`QuotaAccount` is minted per
  *(provider, resolved profile root)*, so two ``CLAUDE_CONFIG_DIR`` roots and
  two ``CODEX_HOME`` roots are four independently refreshable accounts even
  when they share projects. ``account_id`` is a digest of that pair — never an
  email, never anything copied out of a credential.
* **Local and remote answers are separate methods.** :meth:`QuotaProvider.describe`
  is filesystem-only and always cheap; :meth:`QuotaProvider.collect` may reach
  the network. A caller that only wants to enumerate accounts never pays for a
  request, and a provider whose whole answer is local (Codex) simply returns the
  same view from both.
* **The window's DURATION is data, its SCOPE is a coarse grouping.**
  ``SubscriptionWindowView.window_seconds`` carries whatever the provider said, and
  :meth:`QuotaProvider.scope_for_window` buckets it only so a client can lay two
  providers' windows out side by side. Nothing here hard-codes "5h" or "7d" —
  both providers have already moved those boundaries once.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import ClassVar

from grove.core.contracts.usage import (
    BillingAccountView,
    BillingMode,
    QuotaScope,
    QuotaStatus,
    SubscriptionTier,
    UsageProvider,
)

_ID_DIGEST_CHARS = 12
"""Length of the hex tail in an ``account_id``. Twelve hex characters over a
handful of profile roots is a collision probability nobody will ever meet, and
short enough to sit in a URL query string and a config key."""

_WEEKLY_MIN_SECONDS = 86_400
"""A window of a day or more is grouped as ``weekly``, anything shorter as
``session``.

The providers report a duration and no name, so this bucket is Grove's, not
theirs — which is exactly why the real duration stays on the view and this is
only used to decide which column a window renders in. A day is the boundary
because every short window either provider has ever emitted is measured in
hours and every long one in days; a value between the two would be a genuinely
new kind of budget and honestly belongs in neither column."""


@dataclass(frozen=True, slots=True)
class QuotaAccount:
    """One billing identity: a provider plus the profile root that holds its
    credentials.

    ``root`` is kept as a real path because the providers read files under it,
    but it is never put on the wire — ``label`` is what a client shows, and the
    default is the root's own directory name rather than an absolute path
    (a home directory is host-private; a profile's folder name is the thing an
    operator actually recognizes).
    """

    account_id: str
    provider: UsageProvider
    root: Path
    label: str

    @classmethod
    def mint(
        cls,
        *,
        provider: UsageProvider,
        root: Path,
        labels: Mapping[str, str] | None = None,
    ) -> QuotaAccount:
        """Build an account for ``root``, honouring an operator label override.

        The id is derived from the RESOLVED root, so two symlinked paths to one
        profile collapse to a single account rather than double-counting a
        person's quota.
        """
        resolved = resolve_root(root)
        digest = hashlib.sha256(f"{provider}\0{resolved}".encode()).hexdigest()
        return cls._build(
            provider=provider,
            account_id=f"{provider}-{digest[:_ID_DIGEST_CHARS]}",
            root=resolved,
            default_label=resolved.name or str(resolved),
            labels=labels,
        )

    @classmethod
    def from_handle(
        cls,
        *,
        provider: UsageProvider,
        handle: str,
        label: str,
        labels: Mapping[str, str] | None = None,
    ) -> QuotaAccount:
        """Build an aggregate-source account from its stable remote ``handle``.

        Aggregate sources have no credential root to hash. The handle remains
        opaque and is namespaced by its vendor so the shared ledger cannot
        conflate two providers that happen to use the same subscription id.
        """
        account_id = f"{provider}-gateway:{handle}"
        return cls._build(
            provider=provider,
            account_id=account_id,
            root=Path(),
            default_label=label,
            labels=labels,
        )

    @classmethod
    def _build(
        cls,
        *,
        provider: UsageProvider,
        account_id: str,
        root: Path,
        default_label: str,
        labels: Mapping[str, str] | None,
    ) -> QuotaAccount:
        """Construct one account after its identity has been selected."""
        return cls(
            account_id=account_id,
            provider=provider,
            root=root,
            label=(labels or {}).get(account_id) or default_label,
        )


def resolve_root(root: Path) -> Path:
    """``root`` expanded and canonicalized, tolerating a path that is not there.

    ``Path.resolve()`` on a missing path is fine on POSIX but the expansion can
    still raise on a malformed value, and account selection must never be the
    thing that fails a page load.
    """
    try:
        expanded = root.expanduser()
    except (OSError, RuntimeError):
        return root
    try:
        return expanded.resolve()
    except OSError:
        return expanded


class QuotaProvider(ABC):
    """One tool's quota surface: where its profiles live and what they report.

    Concrete providers own their credential-store layout, their endpoint shape
    and their local-evidence parser. They never raise out of
    :meth:`describe`/:meth:`collect` for an expected condition — a failure is a
    :class:`BillingAccountView` carrying the right :class:`QuotaStatus` and a
    one-line ``detail``. The collector still guards against an *unexpected* one.
    """

    provider: ClassVar[UsageProvider]

    metered: ClassVar[bool] = True
    """Does asking this provider cost a request at somebody else's rate limiter?

    The discriminator every caching rule in this package keys off, declared here
    because only the provider knows it. A provider whose evidence is a file its
    own tool already wrote can be re-read as often as a caller likes and can
    never be refused; one that spends an authenticated request per read is the
    entire reason the TTL, the durable ledger and the doubling cool-off exist.
    Defaults to ``True`` so a provider added without anyone thinking about this
    inherits the careful treatment rather than the free one.
    """

    enumerates_accounts: ClassVar[bool] = False
    """Whether this provider discovers its own accounts from one aggregate read."""

    def accounts(
        self, *, labels: Mapping[str, str], known_account_ids: tuple[str, ...]
    ) -> tuple[QuotaAccount, ...]:
        """Accounts this provider can enumerate without a profile-root selection.

        Root-backed providers are selected by ``usage.quota.profiles`` and leave
        this empty. Aggregate sources may use persisted account ids to retain
        their last-known-good views when their roster endpoint is unavailable.
        """
        del labels, known_account_ids
        return ()

    @abstractmethod
    def describe(self, account: QuotaAccount) -> BillingAccountView:
        """This account's posture from LOCAL evidence alone. Never network I/O.

        For a provider whose evidence is entirely local this is the whole
        answer and :meth:`collect` returns the same thing.
        """

    @abstractmethod
    def collect(self, account: QuotaAccount) -> BillingAccountView:
        """This account's posture, reaching the provider where one is reachable.

        Bounded by the timeout the collector was configured with. Reads the
        tool's own credential store at this moment and never copies, persists,
        rewrites or logs what it finds.
        """

    def close(self) -> None:  # noqa: B027 - a provider with no transport has nothing to release
        """Release any transport. A provider with none inherits the no-op."""

    # ─── shared pure helpers ────────────────────────────────────────────────

    @staticmethod
    def scope_for_window(window_seconds: int | None) -> QuotaScope:
        """Bucket a raw window duration for side-by-side rendering.

        ``None`` is ``other`` rather than a guess: a provider that reports a
        percentage with no duration has told us what it governs is unknown.
        """
        if window_seconds is None or window_seconds <= 0:
            return "other"
        return "weekly" if window_seconds >= _WEEKLY_MIN_SECONDS else "session"

    @staticmethod
    def remaining_percent(used_percent: float | None) -> float | None:
        """``100 - used``, clamped, or ``None`` when nothing was reported.

        Clamped because a provider reporting 103% (it happens on a window that
        was exceeded before it was measured) must not render as a negative
        remainder, and because a client draws a bar from this number.
        """
        if used_percent is None:
            return None
        return max(0.0, min(100.0, 100.0 - used_percent))

    def failure(
        self,
        account: QuotaAccount,
        *,
        status: QuotaStatus,
        detail: str,
        billing_mode: BillingMode = "unknown",
        retry_after: datetime | None = None,
        subscription: SubscriptionTier | None = None,
    ) -> BillingAccountView:
        """A view that reports why this account has no windows.

        ``detail`` is a written sentence, never a response body, a header or a
        credential path — the one place a careless implementation would leak.

        ``retry_after`` is the PROVIDER's own instruction about when to come
        back, where it gave one. It rides the view rather than a second return
        value because the field is already on the wire and means the same thing
        at both ends; the collector reconciles it with its own backoff and
        publishes whichever is later.

        ``subscription`` rides a failure too, because which plan an account is
        on is local evidence: a provider that has read the plan out of a
        credential store already knows it, and withholding it exactly when the
        endpoint refuses to answer would blank the one fact that did not need
        the endpoint.
        """
        return BillingAccountView(
            account_id=account.account_id,
            provider=account.provider,
            label=account.label,
            billing_mode=billing_mode,
            subscription=subscription,
            status=status,
            detail=detail,
            retry_after=retry_after,
        )


def observed_age_seconds(observed_at: datetime | None, now: datetime) -> int | None:
    """Whole seconds between an observation and ``now``, never negative.

    A clock skew that puts an observation in the future yields ``0`` rather than
    a negative age, because ``stale_seconds`` is rendered as "N ago".
    """
    if observed_at is None:
        return None
    return max(0, int((now - observed_at).total_seconds()))


def parse_iso_datetime(value: object) -> datetime | None:
    """An ISO-8601 instant from a payload, always returned AWARE.

    Naive input is assumed UTC, matching the transcript parsers: everything
    downstream subtracts these from an aware ``now``, and one naive value raises
    mid-render rather than at the boundary that produced it.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_epoch(value: object, *, unit_ms: bool = False) -> datetime | None:
    """A numeric epoch as an aware datetime, or ``None`` for anything else.

    Both resolutions in one helper because both providers use one each — Codex
    writes ``resets_at`` in seconds, the Claude credential store writes
    ``expiresAt`` in milliseconds — and two nearly-identical parsers is how one
    of them silently acquires a thousand-fold bug.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000 if unit_ms else value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def parse_retry_after(value: str | None, now: datetime) -> datetime | None:
    """An HTTP ``Retry-After`` as an absolute instant, or ``None`` if unusable.

    RFC 9110 allows BOTH a delta in seconds and an HTTP-date, and a client that
    handles only the first silently ignores every server that sends the second —
    which is the failure mode a backoff can least afford, since it looks exactly
    like a server that said nothing. A value already in the past yields ``None``
    rather than a negative wait: it tells us nothing Grove's own schedule does
    not already know.
    """
    if not value:
        return None
    text = value.strip()
    try:
        return now + timedelta(seconds=max(0.0, float(text)))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed is None:  # pragma: no cover - only reachable on very old CPython
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed if parsed > now else None


def as_float(value: object) -> float | None:
    """A reported number, or ``None`` for anything that is not one.

    ``bool`` is excluded explicitly: it is an ``int`` in Python, and a provider
    sending ``true`` where a percentage belongs must read as "not reported"
    rather than as 100% used.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def subscription_tier(plan: object, *, detail: str | None = None) -> SubscriptionTier | None:
    """A provider's own plan token as a tier, or ``None`` when it named none.

    The one place "the provider said nothing" is decided, because both providers
    can report the field as absent, ``null`` or an empty string and all three
    mean the same thing. The token crosses twice — verbatim as ``label`` and
    lowercased as ``plan`` — and is never matched against a list of plans Grove
    expects: a slug nobody here has seen before is a real plan somebody is
    paying for, where a mapping onto the nearest known name is a number on
    somebody's bill that Grove made up.
    """
    if not isinstance(plan, str) or not plan.strip():
        return None
    label = plan.strip()
    return SubscriptionTier(plan=label.lower(), label=label, detail=detail)


__all__ = [
    "QuotaAccount",
    "QuotaProvider",
    "as_float",
    "observed_age_seconds",
    "parse_epoch",
    "parse_iso_datetime",
    "parse_retry_after",
    "resolve_root",
    "subscription_tier",
]
