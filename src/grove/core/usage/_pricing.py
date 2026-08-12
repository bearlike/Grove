"""Cost from tokens — a calculator, kept apart from everything that counts.

Separate from the projector because pricing is the one number in this subsystem
that is *configuration* rather than observation: the same indexed session costs
different money the day an operator edits ``usage.pricing.models``, and nothing
should have to re-ingest for that. The index stores counts; this turns counts
into money at read time (and stamps a per-session figure at write time so the
audit table can sort by it).

Three rules, each of which is a way a dashboard lies about money if you skip it:

**No price configured means ``None``, never zero.** ``CostProvenance.unknown``
renders as a named unknown. A ``$0.00`` beside a million tokens reads as a
measurement.

**Decimal, never float.** These figures are summed across thousands of sessions;
a ledger that drifts in the last place is worse than one that is honestly
absent. The wire carries a decimal STRING for the same reason.

**Reasoning tokens are informational, never a fifth priced class.** Every
provider that reports them bills them inside ``output`` (verified against a real
Codex rollout: ``reasoning_output_tokens`` is a subset of ``output_tokens``), so
adding a reasoning term would double-charge the same tokens.

Provider-reported cost is deliberately absent. The contract ranks it above an
estimate, and it will win the moment a value exists — but the normalized message
spine carries no cost field for any adapter Grove parses today, so a parameter
for it would be a seam with no producer, which reads as "handled" in review and
is not. When one appears (a Claude ``costUSD`` promoted onto ``TokenUsage``, an
OTel span, the wire proxy) it belongs on the *event*, and the precedence lands
in one place: prefer the stored figure, else :meth:`PriceBook.cost`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from grove.core.config import ModelPriceConfig, UsagePricingConfig
from grove.core.contracts.usage import MoneyView

_PER_MILLION = Decimal(1_000_000)

_QUANTUM = Decimal("0.000001")
"""Six decimal places. Prices are quoted per million tokens, so a single cheap
request lands in the fifth or sixth place; rounding earlier would make a
thousand small sessions sum to visibly less than their true total."""


@dataclass(frozen=True, slots=True)
class TokenCounts:
    """The priced token classes for one generation, session or group.

    Mirrors ``TokenClassesView`` minus ``provider_total`` (which is the
    provider's own arithmetic, not an input to Grove's). Every field is
    ``None``-able and a ``None`` contributes nothing — an absent count is not a
    free one, it is an unknown one, and the resulting figure is an
    under-estimate the coverage view is what warns about.
    """

    fresh_input: int | None = None
    cache_read: int | None = None
    cache_creation: int | None = None
    output: int | None = None


class PriceBook:
    """Configured model prices, matched exact-first then longest-prefix.

    The prefix rule is what keeps the config small: ``claude-opus-5`` priced
    once covers ``claude-opus-5-20260401`` and every later snapshot, while an
    exact entry for one dated release still wins over its family. Longest
    prefix, not first match, so ``claude-opus-5-thinking`` cannot be captured by
    a shorter ``claude-`` entry that happens to sort first.
    """

    def __init__(self, cfg: UsagePricingConfig) -> None:
        self._currency = cfg.currency
        self._models: Mapping[str, ModelPriceConfig] = cfg.models
        # Longest first: the first prefix that matches is then the right one.
        self._by_length = sorted(cfg.models, key=len, reverse=True)

    @property
    def currency(self) -> str:
        return self._currency

    @property
    def has_prices(self) -> bool:
        """Whether any price is configured at all — the ``cost_available`` flag
        every coverage view carries, so a client can say "cost not configured"
        instead of drawing a zero."""
        return bool(self._models)

    def price_for(self, model: str | None) -> ModelPriceConfig | None:
        """The price entry governing ``model``, or ``None`` when none does."""
        if not model:
            return None
        exact = self._models.get(model)
        if exact is not None:
            return exact
        for candidate in self._by_length:
            if model.startswith(candidate):
                return self._models[candidate]
        return None

    def amount(self, model: str | None, counts: TokenCounts) -> Decimal | None:
        """The estimated cost of ``counts`` under ``model``'s price, or ``None``.

        ``None`` means *unpriced or unmeasured*, which the caller must render as
        an unknown. A priced model with all-``None`` counts is not a free
        request: there is no token evidence from which to calculate a price.
        """
        price = self.price_for(model)
        if price is None or all(
            value is None
            for value in (
                counts.fresh_input,
                counts.cache_read,
                counts.cache_creation,
                counts.output,
            )
        ):
            return None
        required = (
            (price.input, counts.fresh_input),
            (price.output, counts.output),
            (price.cache_read, counts.cache_read),
            (price.cache_write, counts.cache_creation),
        )
        if any(rate != 0 and value is None for rate, value in required):
            return None
        total = (
            Decimal(str(price.input)) * _tokens(counts.fresh_input)
            + Decimal(str(price.output)) * _tokens(counts.output)
            + Decimal(str(price.cache_read)) * _tokens(counts.cache_read)
            + Decimal(str(price.cache_write)) * _tokens(counts.cache_creation)
        ) / _PER_MILLION
        return total.quantize(_QUANTUM)

    def total(self, groups: Iterable[tuple[str | None, TokenCounts]]) -> Decimal | None:
        """Summed cost across per-model groups, or ``None`` when none priced.

        Aggregates price per MODEL and then add, never the reverse: a range
        spanning two models has no single price, and summing its tokens first
        would charge them all at whichever price happened to be looked up.
        Unpriced groups drop out of the sum rather than zeroing it — a mixed
        range still reports what it can, and coverage says the rest is unknown.
        """
        running: Decimal | None = None
        for model, counts in groups:
            part = self.amount(model, counts)
            if part is None:
                return None
            running = part if running is None else running + part
        return running

    def money(self, amount: Decimal | None) -> MoneyView | None:
        """``amount`` as the wire's decimal-string money, or ``None``.

        Every figure this book produces is ``estimated``: it is Grove
        multiplying counts by a configured price, which for a subscription
        account is a hypothetical, not cash anyone paid.
        """
        if amount is None:
            return None
        return MoneyView(
            amount=format(amount, "f"), currency=self._currency, provenance="estimated"
        )


def _tokens(value: int | None) -> Decimal:
    return Decimal(value) if value else Decimal(0)


def parse_amount(raw: str | None) -> Decimal | None:
    """A stored ``cost_amount`` string back into a ``Decimal``, or ``None``.

    Money round-trips through SQLite as TEXT precisely so it never becomes a
    binary float; this is the one place that text is trusted back, and a value
    that is not a decimal degrades to unknown rather than raising into a render.
    """
    if raw is None:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
