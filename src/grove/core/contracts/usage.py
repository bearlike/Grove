"""Wire shapes for the historical usage audit — tokens, time, cost and quota.

The sibling of ``activity.py``, pointed at the past instead of the present.
``activity.py`` answers *what is happening right now* over a ~1 Hz SSE delta;
this module answers *what happened, across every account and project Grove can
reach*, over bounded request/response reads. Nothing here rides the stream.

Three honesty rules are encoded in the TYPES rather than left to each client,
because every one of them was a way for a dashboard to lie confidently:

* **Token classes never fold.** :class:`TokenClassesView` keeps fresh input,
  cache reads, cache writes, reasoning and output apart, each nullable. A
  client is never asked to guess whether "cached" belongs in input.
* **A duration is two numbers and a confidence, never one.**
  :class:`DurationView` separates the time an agent was *doing* something from
  the birth→last-event span, so nothing can be labelled "time spent".
* **Money carries its provenance and is a decimal STRING.**
  :class:`MoneyView` — a float is not a billing ledger, and a subscription's
  token cost is hypothetical, not billed cash.

Every aggregate response carries a :class:`UsageCoverageView` so a client can
say *how much of the truth this answer contains* — which is what lets one
corrupt transcript or one expired account degrade itself alone.

Unlike ``activity.py``/``sessions.py``, these views have no engine-dataclass
twin and therefore no ``from_*`` classmethods. The usage query service reads
SQLite and constructs these directly: there is no in-process state to hold
between the row and the wire, so a shadow dataclass layer would be pure
duplication. Engine→contracts is the ``TicketRef`` direction and is fine.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Vocabularies
#
# Each is a narrow Literal rather than a bare ``str`` because every one of them
# drives a branch in at least two clients, and an unknown member must be
# visible at the codegen boundary rather than discovered at render time.
# ---------------------------------------------------------------------------

UsageProvider = Literal["claude_code", "codex", "mewbo", "generic"]
"""The agent tool a session ran under. Mirrors ``grove.core.config.AgentKind``.

Duplicated rather than imported so ``contracts`` never drags the engine in at
runtime — the same call ``TodoStatus`` makes in ``sessions.py``, guarded by the
webapp codegen drift-check.
"""

UsageMetric = Literal["tokens", "active_minutes", "sessions", "tool_calls", "cost"]
"""What an activity bucket counts. The heatmap's metric switch, shared by the
web graph and the TUI heatmap so the two cannot disagree about a day's value."""

UsageDimension = Literal["provider", "account", "project", "model", "tool", "token_class"]
"""What a breakdown groups by. One route serves all six rather than one endpoint
per card."""

UsageSessionSort = Literal["recent", "tokens", "cost", "duration", "tool_failures"]
"""Session-table orderings. Narrow because each maps to an indexed SQL column;
a free-form sort key would be an arbitrary-SQL surface by another name."""

CostProvenance = Literal["actual", "provider_reported", "estimated", "unknown"]
"""Where a money figure came from.

``actual`` is cash the provider billed; ``provider_reported`` is a per-request
cost the tool itself wrote into its transcript; ``estimated`` is Grove
multiplying tokens by a configured model price — for a subscription session that
number is **hypothetical**, not money anyone paid. ``unknown`` is the honest
answer when no price is configured for the model, and must render as a named
unknown rather than ``$0``.
"""

DurationConfidence = Literal["measured", "derived", "unknown"]
"""How a duration was obtained. ``measured`` came from OTel or the Grove proxy;
``derived`` was computed from transcript timestamps (a real signal, but not a
request latency); ``unknown`` means the evidence did not support a number."""

QuotaScope = Literal["session", "weekly", "model", "surface", "other"]
"""What a rate-limit window governs. Deliberately NOT named for a duration:
providers move the boundaries, so the duration lives in
``SubscriptionWindowView.window_seconds`` and the scope says what is being
limited.

Names what a READING governs, not how the account it belongs to is billed —
today that reading is always a :class:`SubscriptionWindowView`, since an
``api_key`` account reports :attr:`BillingAccountView.spend` and no window at
all, but the vocabulary describes the governed thing rather than the billing
mode. Kept under the shared ``Quota*`` collection vocabulary rather than
picked up by the subscription-only rename for that reason.
"""

QuotaStatus = Literal["ok", "stale", "unsupported", "auth_expired", "rate_limited", "unreachable"]
"""Per-account collection outcome. Four distinct failures rather than one
``error`` because each takes a different action from the operator: re-login,
wait, upgrade Grove, or check the network. ``stale`` means last-good data is
being shown with an age.

Applies to a probe of EITHER billing mode: a subscription account's window
read and an API-key account's spend read fail and recover through the exact
same outcomes, which is why this stays the shared collection-outcome
vocabulary rather than a subscription-only type.
"""

BillingMode = Literal["subscription", "api_key", "unknown"]
"""How an account pays. A subscription account has quota windows and no cash
figure; an API-key account has spend and **never** a fabricated "remaining".

This is the field that actually draws the line the rest of this module's
naming follows: :class:`SubscriptionWindowView` /
:class:`SubscriptionWindowProjection` exist only where this is
``"subscription"``; :attr:`BillingAccountView.spend` exists only where this
is ``"api_key"``. Every other type here — this one included — spans both
modes on purpose and must not be read as describing raw API-level usage.
"""

QuotaEvidence = Literal["provider_endpoint", "statusline", "rollout", "unknown"]
"""Where a quota snapshot was read. A credential-backed endpoint outranks the
local statusline/rollout fallback, and a client shows which one answered.

Describes the READ, not the account's economics: a subscription window and an
API-key spend figure are both read from one of these same sources, so this
stays shared collection vocabulary rather than a subscription-only type.
"""

SourceHealth = Literal["ok", "degraded", "unreadable"]
"""Per-source ingest health. ``degraded`` means some records were skipped and
the rest are trustworthy; ``unreadable`` means the source contributed nothing."""

FindingKind = Literal[
    "recurring_tool_failure",
    "retry_loop",
    "edit_churn",
    "slow_operation",
    "token_structure",
    "concentration",
]
"""The deterministic detectors. Additive by contract: a client that does not
recognize a kind renders the generic evidence row rather than dropping it."""


# ---------------------------------------------------------------------------
# Value objects — the three honesty rules
# ---------------------------------------------------------------------------


class TokenClassesView(BaseModel):
    """Token counts kept APART, each ``None`` when the provider did not report it.

    Mirrors ``grove.core.agents.TokenUsage`` and widens it with the two facts a
    historical view needs that a per-message spine does not: a provider's own
    ``total`` (which may not equal the sum, and is not Grove's to recompute) and
    the cache-write/read split under the names the audit surfaces use.

    **An absent count is not zero.** A folded ``tokens_in`` is exactly what this
    view exists to stop: cache reads are typically an order of magnitude cheaper
    than fresh input, so summing them hides the only number that moves a bill.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    fresh_input: int | None = None
    """Uncached prompt tokens the provider charged full price for."""

    cache_read: int | None = None
    """Prompt tokens served from a warm cache (Claude ``cache_read``, Codex
    ``cached_input_tokens``)."""

    cache_creation: int | None = None
    """Prompt tokens written INTO the cache — billed at a premium, and the
    number a high-churn context wastes."""

    reasoning: int | None = None
    """Reasoning/thinking tokens where the provider reports them separately.
    Codex reports these; Claude does not, so ``None`` is the common case and
    means *not reported*, never *none used*."""

    output: int | None = None
    """Completion tokens. For providers that fold reasoning into output billing,
    this is the billed figure and ``reasoning`` is the informational split."""

    provider_total: int | None = None
    """The provider's OWN total where it states one. Kept rather than recomputed
    so a client can show the authoritative number beside Grove's sum without
    Grove arbitrating a disagreement it cannot resolve."""


class MoneyView(BaseModel):
    """A money figure that says where it came from and is not a float.

    ``amount`` is a decimal STRING (``"1.2345"``). Binary floats cannot
    represent ordinary decimal money exactly, and these values are summed across
    thousands of sessions — a ledger that drifts in the last place is worse than
    one that is honestly absent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    amount: str
    """Decimal string in ``currency`` units. Never a float, never minor units."""

    currency: str = "USD"
    """ISO 4217 code. Present so a future non-USD provider needs no wire change."""

    provenance: CostProvenance = "unknown"
    """Whether this is billed cash, a provider-reported per-request cost, a
    Grove estimate from configured prices, or genuinely unknown."""


class DurationView(BaseModel):
    """Several durations and a confidence, because one number would be a lie.

    One interval set, two reducers, and the difference between them is the
    fleet. A session's intervals span the root agent AND every sub-agent it
    spawned, which run concurrently:

    * ``active_ms`` is their **union** — real time in which *something* was
      running, with the waits for a human removed. This is a wall clock.
    * ``execution_ms`` is their **sum** — every agent's own time added up, so a
      task that finished in ten minutes by spending ten sub-agents of ten
      minutes each reports ~100 minutes. This is a labour total, not a clock.
    * ``elapsed_span_ms`` is birth→last-event: real, cheap, and **not** how long
      anyone worked. A UI showing only this reports an overnight session as
      sixteen hours of work.

    The invariant that keeps the three honest is
    ``active_ms <= elapsed_span_ms <= wall time`` and ``active_ms <=
    execution_ms``. Grove published a single ``active_ms`` that was the SUM
    while being documented as the union, so on a real fleet session it read
    6.71 h of "active" inside a 4.47 h lifespan (measured 2026-08-11, 11
    sub-agent threads, concurrency factor 2.21x) — a duration longer than the
    session that contained it. **If ``active_ms`` ever exceeds
    ``elapsed_span_ms`` again, the union has silently become a sum.**

    ``generation_ms`` and ``tool_ms`` PARTITION ``execution_ms`` — model wait
    against tool wait — and partition only that one, for the reason each field
    states. A client renders them beside the total, never instead of it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_ms: int | None = None
    """Union of the active intervals — wall-clock time in which the session was
    generating or running tools, human waits excluded and concurrent agents
    counted ONCE. ``None`` when the evidence did not support it."""

    execution_ms: int | None = None
    """Sum of every agent's active intervals, root and sub-agents alike, with
    concurrency counted as many times as it ran. Always ``>= active_ms``; the
    ratio between them is how much parallelism the session actually bought."""

    generation_ms: int | None = None
    """The part of ``execution_ms`` spent waiting on the MODEL — one interval
    per assistant reply, root and sub-agent threads alike.

    Derivable for every provider Grove indexes: a generation's duration is the
    gap between the record that prompted it and the reply itself, which is
    arithmetic over timestamps both Claude Code and Codex write. ``None`` means
    no generation in this session had two timestamps to measure between, never
    that the model answered instantly."""

    tool_ms: int | None = None
    """The part of ``execution_ms`` spent RUNNING TOOLS — one interval per
    ``tool_use`` correlated with its result.

    Together with ``generation_ms`` this partitions ``execution_ms`` exactly:
    the three are one interval set reduced by one reducer, so
    ``generation_ms + tool_ms == execution_ms`` whenever all three are
    measured. The split is what makes "this session was slow" answerable —
    waiting on the model and waiting on a test suite are the same number in
    ``execution_ms`` and completely different problems.

    **There is no ``active_ms`` counterpart and there must not be one.** That
    reducer merges overlaps, and a tool running while a sub-agent generates is
    one span of wall clock that adding two merged halves would count twice —
    the exact double count the union exists to prevent."""

    elapsed_span_ms: int | None = None
    """First event to last event. Always a span, never "time spent"."""

    confidence: DurationConfidence = "unknown"
    """``measured`` (OTel/proxy) outranks ``derived`` (transcript timestamps)."""


class GenerationLatencyView(BaseModel):
    """The model's own average response wait, and how many calls it rests on.

    Distinct from :class:`DurationView`'s ``execution_ms``, which sums
    generation AND tool time together into one labour total — the moment a
    session runs any tools at all, that number stops answering "how slow is
    the model itself". This means just the generation intervals: the gap
    between a request going out and its response landing, one per assistant
    message, averaged. Root and sub-agent generations both count, matching
    ``execution_ms``'s population — the same reduction the historical
    per-model breakdown reads back off ``usage_events``, so a live session
    card and the audit table cannot disagree about what "waiting on the
    model" means.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    avg_ms: int | None = None
    """``None`` when no generation had a measurable interval — never a
    fabricated 0ms average."""

    calls: int = 0
    """How many generation calls the average rests on — an average of one
    call and an average of a thousand are not the same claim."""


# ---------------------------------------------------------------------------
# Coverage — how much of the truth this answer contains
# ---------------------------------------------------------------------------


class UsageSourceView(BaseModel):
    """One indexed store root and its ingest health.

    A source is a ``(provider, profile root)`` pair, not an account: two Claude
    config dirs are two sources even when one person owns both.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    """Opaque stable id for this root. Never a filesystem path — see
    ``label``."""

    provider: UsageProvider
    label: str
    """Human-facing name for the root (an operator label, else a short
    disambiguating tail). Deliberately not the absolute path: a host-private
    home directory is not display data."""

    health: SourceHealth = "ok"
    detail: str | None = None
    """One line naming what degraded, when ``health`` is not ``ok``."""

    session_count: int = 0
    last_indexed_at: datetime | None = None


class UsageCoverageView(BaseModel):
    """What this answer is based on — attached to EVERY aggregate response.

    Without it, a page rendered from a half-indexed store is indistinguishable
    from one rendered from a complete store, and the operator's conclusion
    ("we barely used Codex last week") is wrong for a reason nothing on screen
    can reveal.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    sources: tuple[UsageSourceView, ...] = ()
    degraded_source_count: int = 0
    earliest_event_at: datetime | None = None
    latest_event_at: datetime | None = None
    last_refresh_at: datetime | None = None

    cost_available: bool = False
    """``False`` when no model prices are configured — the client shows cost as
    a named unknown rather than a zero."""

    quota_available: bool = False
    """``False`` when no account could be collected at all."""


# ---------------------------------------------------------------------------
# Filters — one definition, five routes
# ---------------------------------------------------------------------------


class UsageFilters(BaseModel):
    """The filter set every read route accepts, as ONE model.

    Bound as a FastAPI query dependency so the five read routes cannot drift in
    which filters they honour, and so a new dimension is added once.
    ``tz`` shifts BUCKET boundaries and labels only; every timestamp on the wire
    stays aware UTC.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    since: datetime | None = None
    until: datetime | None = None
    tz: str = "UTC"
    """IANA zone name for day-bucket boundaries. Invalid names fall back to UTC
    rather than failing the request — a bad clock preference must not blank the
    page."""

    provider: UsageProvider | None = None
    account: str | None = None
    """An opaque ``BillingAccountView.account_id``, never an email."""

    project: str | None = None
    """A repo root or cwd as reported on ``UsageSessionRowView``."""

    model: str | None = None
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    """``YYYY-MM-DD`` in ``tz`` — the activity graph's day drill-down."""


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class UsageToolStatsView(BaseModel):
    """Tool-call reliability. Failures are counted, never inferred from absence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    calls: int = 0
    failures: int = 0
    distinct_tools: int = 0


class UsageSummaryView(BaseModel):
    """Totals for the filtered range, plus what they are based on.

    Deliberately small. Every field here answers a decision an operator makes;
    a wall of KPI tiles is the failure mode this shape refuses.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    since: datetime | None = None
    until: datetime | None = None
    tz: str = "UTC"

    sessions: int = 0
    turns: int = 0
    tokens: TokenClassesView = TokenClassesView()
    duration: DurationView = DurationView()
    tools: UsageToolStatsView = UsageToolStatsView()
    files_changed: int = 0
    cost: MoneyView | None = None
    """``None`` when no price is configured for any model in range — a named
    unknown, never a zero."""

    accounts: int = 0
    projects: int = 0
    coverage: UsageCoverageView = UsageCoverageView()


# ---------------------------------------------------------------------------
# Activity — the contribution graph / heatmap
# ---------------------------------------------------------------------------


class UsageActivityBucketView(BaseModel):
    """One day of the contribution graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    """``YYYY-MM-DD`` in the request's ``tz``. A date string rather than a
    timestamp because it IS a calendar day in a chosen zone, and re-deriving
    that from an instant is how the web graph and the TUI heatmap would come to
    disagree."""

    value: float = 0.0
    """The metric's value. ``float`` because ``active_minutes`` and ``cost`` are
    fractional; token/session/tool metrics arrive as whole numbers."""

    sessions: int = 0
    """Always present regardless of ``metric`` — the day tooltip needs it and it
    costs nothing."""


class UsageActivityView(BaseModel):
    """The full day series for one metric."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: UsageMetric
    tz: str = "UTC"
    buckets: tuple[UsageActivityBucketView, ...] = ()
    total: float = 0.0
    max_value: float = 0.0
    """The series maximum, so both renderers scale intensity identically instead
    of each picking its own ceiling."""

    coverage: UsageCoverageView = UsageCoverageView()


# ---------------------------------------------------------------------------
# Sessions — the audit table
# ---------------------------------------------------------------------------


class UsageSessionRowView(BaseModel):
    """One session in the audit table.

    Carries the exact ``(kind, cwd, session_id)`` coordinate the existing
    read-only session-detail route resolves by, so a row LINKS to the transcript
    Grove already renders instead of this page rebuilding one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    provider: UsageProvider
    cwd: str | None = None
    """The session's working directory — one third of the drill-in coordinate,
    and the only thing that places a row. Null only when the source never
    recorded one."""

    project: str | None = None
    """Resolved repo root, when the cwd sits under one."""

    account_id: str | None = None
    account_label: str | None = None
    source_id: str | None = None

    models: tuple[str, ...] = ()
    """Every model the session used, in first-seen order. A session that
    switched models has two, and folding to one would misattribute cost."""

    started_at: datetime | None = None
    last_event_at: datetime | None = None
    duration: DurationView = DurationView()

    turns: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    files_changed: int = 0
    tokens: TokenClassesView = TokenClassesView()
    """This session's totals, root work AND everything it delegated to
    sub-agents summed together — a session's total includes the work it
    delegated, so this number never reads low against what the transcripts
    actually show."""

    subagent_tokens: TokenClassesView | None = None
    """The portion of ``tokens`` ABOVE attributable to sub-agents alone — a
    partition of the same sum, not a second measurement. Lets a reader say
    "of the total, this much was delegated" without Grove folding root and
    sub-agent work into one opaque figure. ``None`` when no sub-agent usage
    was measured for this session (no sub-agents ran, or none reported
    tokens) — never a fabricated ``0``."""

    cost: MoneyView | None = None

    parser_health: SourceHealth = "ok"
    parser_detail: str | None = None
    """Why this row is incomplete, when it is. A row that silently lost half its
    records reads exactly like a quiet session."""


class UsageSessionPageView(BaseModel):
    """A cursor-paginated page of audit rows."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rows: tuple[UsageSessionRowView, ...] = ()
    next_cursor: str | None = None
    """Opaque; ``None`` on the last page. Cursor rather than offset so a page
    stays stable while a refresh appends rows underneath it."""

    sort: UsageSessionSort = "recent"
    coverage: UsageCoverageView = UsageCoverageView()


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------


class UsageBreakdownRowView(BaseModel):
    """One group in a breakdown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    """The raw grouping value — a provider name, an account id, a repo root, a
    model id, a tool name, or a token-class name."""

    label: str
    """Display form. Separate from ``key`` because a client filters by ``key``
    and must not have to reverse a prettified label to do it."""

    sessions: int = 0
    tokens: TokenClassesView = TokenClassesView()
    active_ms: int | None = None
    tool_calls: int = 0
    tool_failures: int = 0
    cost: MoneyView | None = None
    latency: GenerationLatencyView = GenerationLatencyView()
    """Populated on the ``model`` dimension only — every other dimension's rows
    default to the empty view (``avg_ms=None, calls=0``), which reads as
    "not measured" rather than as a claim about a tool or a token class."""


class UsageBreakdownView(BaseModel):
    """Composition along one dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: UsageDimension
    rows: tuple[UsageBreakdownRowView, ...] = ()
    truncated: bool = False
    """``True`` when a long tail was dropped past the row cap. Stated rather
    than silent — a truncated breakdown that looks complete is the "we only use
    three models" conclusion drawn from a capped list."""

    coverage: UsageCoverageView = UsageCoverageView()


# ---------------------------------------------------------------------------
# Series — a breakdown that keeps its days
# ---------------------------------------------------------------------------


class UsageSeriesPointView(BaseModel):
    """One day of one group's series."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    day: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    """``YYYY-MM-DD`` in the request's ``tz`` — a calendar day in a chosen zone,
    for the reason :class:`UsageActivityBucketView` states."""

    value: float | None = None
    """``None`` where nothing reported this group on this day, which is NOT the
    same as a measured zero and must not render as a bar of height nothing."""


class UsageSeriesGroupView(BaseModel):
    """One group's whole series across the window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    """The raw grouping value, as :class:`UsageBreakdownRowView.key` defines it —
    a client filters by this and must not reverse a label to do it."""

    label: str
    total: float | None = None
    points: tuple[UsageSeriesPointView, ...] = ()
    """One point per entry in :attr:`UsageSeriesView.days`, same order, gaps
    included — so a renderer zips the two rather than joining on the date."""


class UsageSeriesView(BaseModel):
    """A metric over days, split by one dimension — the breakdown that kept time.

    :class:`UsageActivityView` is one metric per day with no split;
    :class:`UsageBreakdownView` is one total per group with no days. This is the
    cell between them, and it exists because "which model is eating the week"
    is a question neither can answer.

    The ``days`` spine is published rather than derived so every renderer draws
    the same axis, including days on which nothing ran. A client that builds its
    own spine from the points it received draws a chart whose gaps close up.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: UsageMetric
    dimension: UsageDimension
    tz: str = "UTC"

    days: tuple[str, ...] = ()
    """The calendar spine, ascending and contiguous — the x-axis itself."""

    groups: tuple[UsageSeriesGroupView, ...] = ()
    truncated: bool = False
    """``True`` when a long tail was dropped past the group cap, for the reason
    :attr:`UsageBreakdownView.truncated` states."""

    coverage: UsageCoverageView = UsageCoverageView()


# ---------------------------------------------------------------------------
# Quota and billing
# ---------------------------------------------------------------------------


class SubscriptionTier(BaseModel):
    """Which paid plan an account is on, as the provider itself words it.

    Genuinely subscription-only: populated only when
    :attr:`BillingAccountView.billing_mode` is ``"subscription"``. An
    ``api_key`` account has no plan tier to report — it has
    :attr:`BillingAccountView.spend` instead — so this is never a stand-in for
    API-level usage.

    Absent means Grove could not tell. There is no "unknown" plan string and no
    default, because this is the one field a person reconciles against their own
    bill: a plan named wrongly is worse than a plan left blank. Every value here
    is one the provider wrote down — never inferred from a window size, a token
    limit or how much quota an account appears to have.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: str | None = None
    """The plan as a lowercase slug — ``max``, ``pro``, ``team``, ``plus``,
    ``free``. Trimmed and lowercased, never mapped onto a vocabulary of Grove's
    own: a plan name Grove has not seen before crosses unchanged rather than
    becoming the nearest one it recognizes."""

    label: str | None = None
    """The provider's own wording, verbatim. Kept beside the slug so a client
    can show what the vendor calls this plan without Grove expanding, prettifying
    or translating a name it does not own."""

    detail: str | None = None
    """The sub-tier, where the provider names one — ``20x`` for a Claude Max
    account. Absent when the plan has no sub-tier, or when the provider did not
    say."""


class SubscriptionWindowProjection(BaseModel):
    """Whether this window's burn rate lands under the limit before it resets.

    Genuinely subscription-only, like the :class:`SubscriptionWindowView` it
    rides on: a burn-rate forecast only makes sense against a plan's own reset
    cadence, and an ``api_key`` account has no window to project — it has
    :attr:`BillingAccountView.spend` instead. Nothing here describes raw
    API-level usage; it is Grove's own arithmetic over one plan's consumption.

    Arithmetic over the reading already on the window — no provider is
    contacted to produce it. Every field is nullable and stays ``None``
    whenever the window has not reported enough to answer: a window with no
    known duration, one that has barely begun, or one with no usage figure
    reads ``unknown`` rather than publishing a projection built on a guess.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    elapsed_percent: float | None = None
    """How far through the window the moment of reading is."""

    resolved_window_seconds: int | None = None
    """The duration this projection was actually computed against.

    Separate from :attr:`SubscriptionWindowView.window_seconds`, which stays the
    PROVIDER's own statement, because these two can legitimately differ: the
    Claude endpoint publishes no duration at all, so a window there is projected
    against the operator's ``usage.quota.window_seconds`` assertion instead. One
    field carrying both would make "the vendor said this" and "you told us this"
    indistinguishable, and only the first is evidence.

    It rides here rather than on the window because a projection is by
    definition Grove's own arithmetic — and because any client normalizing this
    window to some other span (tokens per day, say) needs the SAME denominator
    the verdict beside it was computed from, or the two disagree on screen.
    ``None`` whenever nobody, vendor or operator, said how long the window is.
    """

    burn_rate: float | None = None
    """Usage divided by elapsed time. ``1.0`` is exactly on pace to finish the
    window at the limit; below one leaves headroom, above one overruns."""

    projected_percent: float | None = None
    """Usage at reset if the current pace holds for the rest of the window."""

    verdict: Literal["on_track", "tight", "over", "unknown"] = "unknown"
    """The projection read as a judgement, against configurable thresholds.
    ``unknown`` means the window has not said enough to be judged."""

    exhausts_at: datetime | None = None
    """When this pace reaches the limit, and only when it does so before the
    window resets. A window with headroom leaves this empty rather than naming
    a date that would read as the moment the account runs out."""

    tokens_used: int | None = None
    """Tokens measured for this account inside the window's own time bounds,
    from the transcripts Grove has already indexed. ``None`` — never zero —
    when no session in the window carries token evidence."""

    tokens_available_estimate: int | None = None
    """An ESTIMATE of the window's whole token allowance, extrapolated from the
    measured tokens and the reported percentage. The providers publish a
    percentage and no token budget, so this is Grove's arithmetic rather than a
    figure anyone has quoted, and it is only as accurate as that percentage."""


class SubscriptionWindowView(BaseModel):
    """One subscription rate-limit window for one account.

    Genuinely subscription-only: a window exists only for a
    :attr:`BillingAccountView.billing_mode` of ``"subscription"``. An
    ``api_key`` account reports :attr:`BillingAccountView.spend` instead and
    never populates this — nothing here is, or stands in for, raw API-level
    usage.

    ``window_seconds`` carries the provider's OWN duration rather than Grove
    hard-coding "5h" and "7d" — those boundaries have moved before and the
    label a client renders should follow the provider, not a constant.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: QuotaScope
    label: str
    """The provider's own name for the window, where it gives one."""

    window_seconds: int | None = None
    used_percent: float | None = None
    remaining_percent: float | None = None
    resets_at: datetime | None = None

    limit: float | None = None
    used: float | None = None
    unit: str | None = None
    """Only for providers that report absolute figures. Most report percentages
    alone, and inventing a unit for them would be fabrication."""

    observed_at: datetime | None = None
    evidence: QuotaEvidence = "unknown"

    projection: SubscriptionWindowProjection | None = None
    """How this window's burn rate lands against its own reset. Derived at read
    time from the fields above, so it follows the clock without a new reading."""


class BillingAccountView(BaseModel):
    """One billing identity, with its windows and its collection outcome.

    Spans BOTH billing modes on purpose — this is never itself a
    subscription-only shape. ``billing_mode`` says which one this account is;
    ``windows`` (subscription-only, see :class:`SubscriptionWindowView`) and
    ``spend`` (API-key-only) populate accordingly, and every other field here
    describes the account or the collection attempt, not raw API-level usage.

    An account is selected from a configured profile root, so two
    Claude config dirs and two Codex homes are FOUR accounts even when they
    share projects. ``account_id`` is an opaque local id derived from the
    profile — never an email, never a token, never anything copied out of a
    credential store.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_id: str
    provider: UsageProvider
    label: str
    """Operator-settable display name, defaulting to a short disambiguator."""

    billing_mode: BillingMode = "unknown"
    subscription: SubscriptionTier | None = None
    """Which paid plan this account is on. Absent means Grove could not tell."""

    status: QuotaStatus = "unsupported"
    detail: str | None = None
    """One line naming the failure, when ``status`` is not ``ok``/``stale``.
    Never a raw response body, never a header."""

    windows: tuple[SubscriptionWindowView, ...] = ()
    """Subscription accounts only — empty for an ``api_key`` account, which
    reports ``spend`` below instead."""

    spend: MoneyView | None = None
    """API-key accounts only. A subscription account leaves this ``None`` rather
    than presenting an estimated token cost as money owed."""

    observed_at: datetime | None = None
    stale_seconds: int | None = None
    """Age of the last-good snapshot being shown. Present whenever ``status`` is
    ``stale``, so the page keeps answering through a transient 401/429 while
    saying plainly that it is."""

    last_error: QuotaStatus | None = None
    """Why the most recent refresh did not produce this reading.

    ``status`` says what is on the page; this says what happened when Grove last
    tried to make it newer. Without it ``stale`` collapses a rate limit, an
    expired login and a dropped network into one word, and only one of the three
    means *stop asking*. ``None`` whenever the reading on show is the one that
    was just collected."""

    retry_after: datetime | None = None
    """When Grove will next contact this provider for this account.

    An instant rather than a countdown, for the reason a provisioning elapsed is
    sent raw: a server-computed remainder is stale the moment it leaves the
    daemon and sits visibly frozen between polls. Sourced from the provider's own
    ``Retry-After`` where it sends one, and from Grove's backoff otherwise."""


class UsageQuotasView(BaseModel):
    """Every explicitly selected account's latest snapshot.

    Spans both billing modes — a subscription account's windows and an
    API-key account's spend both ride here, distinguished by
    :attr:`BillingAccountView.billing_mode`. Not itself a subscription-only
    shape, even though most of its individual windows are.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts: tuple[BillingAccountView, ...] = ()
    coverage: UsageCoverageView = UsageCoverageView()


# ---------------------------------------------------------------------------
# Insights
# ---------------------------------------------------------------------------


class UsageFindingView(BaseModel):
    """One deterministic, evidence-backed finding.

    Never prose from a model. Each finding names the filter that reproduces it,
    so a click lands on the sessions that produced it rather than asking the
    reader to trust a number.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: FindingKind
    title: str
    detail: str | None = None

    count: int = 0
    """Occurrences — the recurrence half of the ranking."""

    impact: float = 0.0
    """Normalized 0..1 magnitude. Deliberately unitless: mixing "tokens wasted"
    and "minutes lost" into one currency would need a dollar figure Grove often
    does not honestly have."""

    confidence: float = 0.0
    """0..1. A detector whose provider did not report the needed fact lowers
    this or suppresses itself; it never fabricates the evidence."""

    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None

    evidence_filters: UsageFilters = UsageFilters()
    """The exact filter set that reproduces this finding on the sessions route."""

    session_ids: tuple[str, ...] = ()
    """A bounded sample of contributing sessions, for a direct drill-in."""

    subject: str | None = None
    """What the finding is ABOUT — a tool name, a file path, a model id. Already
    metadata elsewhere in the index; never prompt or result text."""


class UsageFindingsView(BaseModel):
    """Ranked findings for the filtered range."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    findings: tuple[UsageFindingView, ...] = ()

    total: int = 0
    """How many findings the detectors produced BEFORE any page limit — so a
    client can say "showing 50 of 2475" rather than presenting a truncated list
    as the whole answer.

    The list is RANKED, so the tail is the least interesting part by
    construction and a client is expected to render a head. That is exactly why
    the count must cross: a silently short list reads as a quiet range, which is
    the opposite of what a 2475-finding range means. ``total == len(findings)``
    whenever nothing was withheld.
    """

    coverage: UsageCoverageView = UsageCoverageView()


class UsageBashCommandView(BaseModel):
    """One executable's share of the time spent inside shell tool calls.

    Read this as **"time in shell calls LED BY this command"**, never as "time
    spent in this command". A shell call is a whole command line — pipes,
    `&&` chains, loops — and the audit attributes the call's entire duration to
    the leading top-level executable, because that is the only attribution
    whose total equals real wall clock. Crediting every executable in a call
    was measured at **3.05x** the true total. The visible cost of the rule is
    that `find … | xargs pylint` is credited to `find`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    executable: str
    """The normalized leading command — basename, version suffix folded."""

    calls: int = 0
    total_ms: int = 0
    avg_ms: int = 0

    censored_calls: int = 0
    """Calls whose duration sits at or above the harness's own timeout ceiling.

    Those durations say when the tool gave up, not what the command cost, and
    real durations cluster tightly just above the ceiling. Counted separately
    so a ranking dominated by them can be seen to be, rather than read as
    measured cost."""

    error_calls: int = 0
    """Calls whose tool result carried a STRUCTURAL error flag.

    Not "the bash equivalent of a non-zero exit", which is what this field
    claimed until 2026-08-11 and is false for every harness that records no
    such flag: a failed call there is indistinguishable from a successful one
    and counts as neither. Read it only against ``error_reportable_calls``.
    """

    error_reportable_calls: int = 0
    """Of ``calls``, how many ran under a harness that can report a tool error
    at all — the honest denominator for ``error_calls``.

    The capability is declared per adapter (``AgentAdapter.reports_tool_errors``)
    and is not uniform: Claude Code fills ``is_error`` natively while Codex's
    tool-output record has no error key in any version, so every Codex call
    contributes to ``calls`` and can only ever contribute ``0`` to
    ``error_calls``. Dividing by ``calls`` therefore deflates a mixed row's
    failure rate by exactly that row's silent share — invisibly, and differently
    per row, since the mix varies by command. ``0`` is a real measurement and
    means the whole row is unmeasurable for errors, which a client must render
    as *not measured* rather than as a confident ``0%``."""

    background_calls: int = 0
    """Calls the harness was asked to detach.

    The result returns near-instantly with a handle, so the measured duration
    is the LAUNCH and the true completion time is invisible. Long-running
    commands are therefore biased DOWNWARD in this ranking by however many of
    these they have."""


class UsageBashInsightView(BaseModel):
    """Where the agent's shell time went, ranked, with its own caveats attached.

    Both caveat counters ride every row AND the header on purpose: without
    them a ranking of censored, backgrounded calls is indistinguishable from a
    ranking of measured cost, and a reader acts on the two very differently.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    commands: tuple[UsageBashCommandView, ...] = ()
    """Ranked by ``total_ms`` and bounded to a top-N; the tail of a ranked list
    is noise by construction."""

    unattributed_calls: int = 0
    """Shell calls whose leading command could not be resolved — a line that is
    only builtins, only a loop header, or that no road could read. Reported
    rather than dropped, so the ranked rows are never mistaken for the whole
    population."""

    unattributed_ms: int = 0

    total_calls: int = 0
    """Every measured shell call in range, attributed or not — the denominator
    the ranked rows are a share of."""

    total_ms: int = 0


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


class UsageRefreshView(BaseModel):
    """The outcome of ``POST /usage/refresh`` — status, never the dataset.

    Returning the refreshed data here would make every client's refresh a second
    full page load, and would make the coalescer's "your call joined an
    in-flight refresh" answer either a lie or a different shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    indexed_sources: int = 0
    changed_sources: int = 0
    """How many sources actually had new bytes. Zero is the common, cheap case
    and is worth showing: it is the evidence that an unchanged refresh reparsed
    nothing."""

    quota_accounts: int = 0
    quota_failures: int = 0
    duration_ms: int = 0
    coalesced: bool = False
    """``True`` when this call joined an already-running refresh instead of
    starting one — two clients opening the page must not launch two host
    scans."""

    coverage: UsageCoverageView = UsageCoverageView()
