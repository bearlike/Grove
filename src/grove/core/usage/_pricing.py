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
from types import MappingProxyType

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
        self.replace(cfg)

    def replace(self, cfg: UsagePricingConfig) -> None:
        """Atomically install one immutable configuration snapshot."""
        manual_models = MappingProxyType(dict(cfg.models))
        # Assignment is the atomic transition: callers retain either the old
        # complete tuple or the new one, never a mixture while aggregating.
        self._snapshot = (
            cfg.currency,
            manual_models,
            tuple(sorted(manual_models, key=len, reverse=True)),
            frozenset(cfg.fetched_models),
            MappingProxyType(dict(cfg.aliases)),
            frozenset(cfg.models).difference(cfg.fetched_models),
        )

    def snapshot(self) -> PriceBook:
        """Return a stable book for an aggregate spanning one price revision."""
        result = object.__new__(PriceBook)
        result._snapshot = self._snapshot
        return result

    @property
    def _currency(self) -> str:
        return self._snapshot[0]

    @property
    def _models(self) -> Mapping[str, ModelPriceConfig]:
        return self._snapshot[1]

    @property
    def _by_length(self) -> tuple[str, ...]:
        return self._snapshot[2]

    @property
    def _fetched_models(self) -> frozenset[str]:
        return self._snapshot[3]

    @property
    def _aliases(self) -> Mapping[str, str]:
        return self._snapshot[4]

    @property
    def _manual_models(self) -> frozenset[str]:
        return self._snapshot[5]

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
        return self._price_for(model, self._snapshot)

    @staticmethod
    def _price_for(
        model: str | None,
        snapshot: tuple[
            str,
            Mapping[str, ModelPriceConfig],
            tuple[str, ...],
            frozenset[str],
            Mapping[str, str],
            frozenset[str],
        ],
    ) -> ModelPriceConfig | None:
        if not model:
            return None
        _, models, by_length, fetched_models, aliases, manual_models = snapshot
        # An operator's exact spelling is deliberate. Alias normalization only
        # applies when it would not displace that explicit manual override.
        if model in manual_models:
            return models[model]
        while model in aliases:
            model = aliases[model]
        exact = models.get(model)
        if exact is not None:
            return exact
        # A source's entry names one deployment exactly. Prefix matching it would
        # silently charge an unrelated deployment that happened to share a stem.
        for candidate in by_length:
            if candidate not in fetched_models and model.startswith(candidate):
                return models[candidate]
        return None

    def amount(self, model: str | None, counts: TokenCounts) -> Decimal | None:
        """The estimated cost of ``counts`` under ``model``'s price, or ``None``.

        ``None`` means *unpriced or unmeasured*, which the caller must render as
        an unknown. A priced model with all-``None`` counts is not a free
        request: there is no token evidence from which to calculate a price.
        """
        snapshot = self._snapshot
        return self._amount(model, counts, snapshot)

    @classmethod
    def _amount(
        cls,
        model: str | None,
        counts: TokenCounts,
        snapshot: tuple[
            str,
            Mapping[str, ModelPriceConfig],
            tuple[str, ...],
            frozenset[str],
            Mapping[str, str],
            frozenset[str],
        ],
    ) -> Decimal | None:
        # Explicitly measured zero usage needs no model rate. Missing counts
        # remain unknown; in particular None must never compare as free usage.
        if all(
            value == 0
            for value in (
                counts.fresh_input,
                counts.cache_read,
                counts.cache_creation,
                counts.output,
            )
        ):
            return Decimal(0).quantize(_QUANTUM)
        price = cls._price_for(model, snapshot)
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
        if any(rate is None and value != 0 for rate, value in required):
            return None
        if any(rate not in (None, 0) and value is None for rate, value in required):
            return None
        total = (
            _rate_amount(price.input, counts.fresh_input)
            + _rate_amount(price.output, counts.output)
            + _rate_amount(price.cache_read, counts.cache_read)
            + _rate_amount(price.cache_write, counts.cache_creation)
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
        snapshot = self._snapshot
        running: Decimal | None = None
        for model, counts in groups:
            part = self._amount(model, counts, snapshot)
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
        currency = self._snapshot[0]
        return MoneyView(amount=format(amount, "f"), currency=currency, provenance="estimated")


def _tokens(value: int | None) -> Decimal:
    return Decimal(value) if value else Decimal(0)


def _rate_amount(rate: float | None, tokens: int | None) -> Decimal:
    """A known zero or missing rate can price only zero-or-absent evidence."""
    if rate is None:
        return Decimal(0)
    return Decimal(str(rate)) * _tokens(tokens)


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
