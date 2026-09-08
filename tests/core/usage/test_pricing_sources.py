"""Pricing source snapshots keep estimates honest without retaining secrets."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from grove.core.config import ModelPriceConfig, UsagePricingConfig
from grove.core.usage._pricing import PriceBook, TokenCounts
from grove.core.usage.pricing_sources import PricingCatalog, _SourceSnapshot


def _cfg(**pricing: object) -> UsagePricingConfig:
    return UsagePricingConfig.model_validate(
        {
            "sources": [
                {
                    "base_url": "https://prices.example.test",
                    "token_env": "PRICE_TOKEN",
                }
            ],
            **pricing,
        }
    )


def _response() -> dict[str, object]:
    return {
        "data": [
            {
                "model_name": "deployed-model",
                "input_cost_per_token": 0.000001,
                "output_cost_per_token": 0.000002,
                "cache_read_input_token_cost": 0,
            }
        ]
    }


def test_refresh_fetches_normalized_snapshot_with_bearer_and_correct_endpoint(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_response())

    cfg = _cfg()
    resolved = _catalog(
        cfg,
        cache_path=tmp_path / "prices.json",
        environ={"PRICE_TOKEN": "not-in-cache"},
        handler=handler,
    ).refresh()

    assert str(seen[0].url) == "https://prices.example.test/v1/model/info"
    assert seen[0].headers["authorization"] == "Bearer not-in-cache"
    assert resolved.models["deployed-model"] == ModelPriceConfig(
        input=1.0, output=2.0, cache_read=0.0, cache_write=None
    )
    cache = (tmp_path / "prices.json").read_text()
    assert "not-in-cache" not in cache
    assert "PRICE_TOKEN" in cache


def test_default_pricing_does_not_create_cache_state(tmp_path: Path) -> None:
    catalog = PricingCatalog(UsagePricingConfig(), cache_path=tmp_path / "missing" / "prices.json")
    assert catalog.load().models == {}
    assert catalog.refresh().models == {}
    assert not list(tmp_path.iterdir())


def test_remote_usd_rates_cannot_be_relabelled_and_aliases_are_nonblank() -> None:
    with pytest.raises(ValidationError, match="USD"):
        _cfg(currency="EUR")
    for aliases in ({"": "model"}, {"model": " "}):
        with pytest.raises(ValidationError, match="blank"):
            _cfg(aliases=aliases)


def test_source_root_with_v1_is_not_doubled(tmp_path: Path) -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"data": []})

    _catalog(
        _cfg(sources=[{"base_url": "https://prices.example.test/v1", "token_env": "PRICE_TOKEN"}]),
        cache_path=tmp_path / "prices.json",
        environ={"PRICE_TOKEN": "value"},
        handler=handler,
    ).refresh()
    assert urls == ["https://prices.example.test/v1/model/info"]


def test_load_is_cache_only_and_config_fingerprint_prevents_cross_source_reuse(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "prices.json"
    source = _cfg()
    seed = PricingCatalog(source, cache_path=cache)
    seed._clock = lambda: 10
    seed._environ = {"PRICE_TOKEN": "x"}
    seed._write_cache({"https://prices.example.test\nPRICE_TOKEN": _snapshot(10)})
    loaded = _catalog(
        source,
        cache_path=cache,
        clock=lambda: 11,
        handler=lambda _: (_ for _ in ()).throw(AssertionError("load must not fetch")),
    ).load()
    other_catalog = PricingCatalog(
        _cfg(sources=[{"base_url": "https://other.example.test", "token_env": "PRICE_TOKEN"}]),
        cache_path=cache,
    )
    other_catalog._clock = lambda: 11
    other = other_catalog.load()

    assert "deployed-model" in loaded.models
    assert "deployed-model" not in other.models


def test_expired_or_failed_source_withholds_fetched_rates_but_preserves_manual(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "prices.json"
    cfg = _cfg(models={"manual": {"input": 7}}, cache_ttl_seconds=10)
    catalog = PricingCatalog(cfg, cache_path=cache)
    catalog._clock = lambda: 100
    catalog._environ = {"PRICE_TOKEN": "x"}
    catalog._write_cache({"https://prices.example.test\nPRICE_TOKEN": _snapshot(1)})
    failing = _catalog(
        cfg,
        cache_path=cache,
        clock=lambda: 100,
        environ={"PRICE_TOKEN": "x"},
        handler=lambda _: (_ for _ in ()).throw(httpx.ConnectError("down")),
    )

    refreshed = failing.refresh()
    assert set(refreshed.models) == {"manual"}


def test_conflicting_deployments_and_sources_are_withheld(tmp_path: Path) -> None:
    cfg = _cfg(
        sources=[
            {"base_url": "https://one.example.test", "token_env": "ONE"},
            {"base_url": "https://two.example.test", "token_env": "TWO"},
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        rate = 0.000001 if "one" in str(request.url) else 0.000002
        return httpx.Response(
            200,
            json={"data": [{"model_name": "same", "input_cost_per_token": rate}]},
        )

    result = _catalog(
        cfg,
        cache_path=tmp_path / "prices.json",
        environ={"ONE": "a", "TWO": "b"},
        handler=handler,
    ).refresh()
    assert "same" not in result.models


def test_duplicate_model_name_with_conflicting_rate_is_withheld(tmp_path: Path) -> None:
    result = _catalog(
        _cfg(),
        cache_path=tmp_path / "prices.json",
        environ={"PRICE_TOKEN": "x"},
        handler=lambda _: httpx.Response(
            200,
            json={
                "data": [
                    {"model_name": "same", "input_cost_per_token": 0.000001},
                    {"model_name": "same", "input_cost_per_token": 0.000002},
                ]
            },
        ),
    ).refresh()
    assert "same" not in result.models


def test_duplicate_model_name_with_identical_rate_is_valid(tmp_path: Path) -> None:
    result = _catalog(
        _cfg(),
        cache_path=tmp_path / "prices.json",
        environ={"PRICE_TOKEN": "x"},
        handler=lambda _: httpx.Response(
            200,
            json={
                "data": [
                    {"model_name": "same", "input_cost_per_token": 0.000001},
                    {"model_name": "same", "input_cost_per_token": 0.000001},
                ]
            },
        ),
    ).refresh()
    assert result.models["same"].input == 1.0


def test_manual_exact_override_aliases_and_fetched_exact_matching() -> None:
    cfg = UsagePricingConfig(
        models={"manual": ModelPriceConfig(input=3)},
        aliases={"reported": "manual"},
    ).with_fetched_models(
        {
            "manual": ModelPriceConfig(input=3),
            "fetched": ModelPriceConfig(input=1, output=None, cache_read=None, cache_write=None),
        },
        frozenset({"fetched"}),
    )
    book = PriceBook(cfg)

    assert book.price_for("reported") == ModelPriceConfig(input=3)
    assert book.price_for("manual-release") == ModelPriceConfig(input=3)
    assert book.price_for("fetched-release") is None


def test_unknown_rate_only_priced_for_measured_zero_and_all_missing_is_unknown() -> None:
    book = PriceBook(
        UsagePricingConfig().with_fetched_models(
            {"model": ModelPriceConfig(input=None, output=2, cache_read=None, cache_write=None)},
            frozenset({"model"}),
        )
    )

    assert book.amount(
        "model", TokenCounts(fresh_input=0, output=1_000_000, cache_read=0, cache_creation=0)
    ) == Decimal("2.000000")
    assert book.amount("model", TokenCounts(fresh_input=1, output=1_000_000)) is None
    assert book.amount("model", TokenCounts()) is None
    assert book.amount("model", TokenCounts(fresh_input=0, output=None)) is None


@pytest.mark.parametrize("model", [None, "unconfigured"])
def test_explicit_zero_tokens_need_no_model_price(model: str | None) -> None:
    book = PriceBook(UsagePricingConfig())
    assert book.amount(model, TokenCounts(0, 0, 0, 0)) == Decimal("0.000000")
    assert book.amount(model, TokenCounts(0, 0, None, 0)) is None
    assert book.amount(model, TokenCounts(1, 0, 0, 0)) is None


def test_alias_cycles_and_invalid_rates_are_rejected() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        UsagePricingConfig(aliases={"one": "two", "two": "one"})
    with pytest.raises(ValidationError, match="finite and nonnegative"):
        ModelPriceConfig(input=-1)


def test_replace_does_not_change_existing_snapshot() -> None:
    book = PriceBook(UsagePricingConfig(models={"model": ModelPriceConfig(input=1)}))
    before = book.snapshot()
    book.replace(UsagePricingConfig(models={"model": ModelPriceConfig(input=2)}))

    counts = TokenCounts(fresh_input=1_000_000, output=0, cache_read=0, cache_creation=0)
    assert before.amount("model", counts) == Decimal("1.000000")
    assert book.amount("model", counts) == Decimal("2.000000")


def _catalog(
    cfg: UsagePricingConfig,
    *,
    cache_path: Path,
    environ: dict[str, str] | None = None,
    handler: Callable[[httpx.Request], httpx.Response],
    clock: Callable[[], float] = lambda: 0,
) -> PricingCatalog:
    catalog = PricingCatalog(cfg, cache_path=cache_path)
    catalog._environ = environ or {}
    catalog._clock = clock
    catalog._transport = httpx.MockTransport(handler)
    return catalog


def _snapshot(at: float) -> _SourceSnapshot:
    return _SourceSnapshot(
        "https://prices.example.test\nPRICE_TOKEN",
        at,
        {"deployed-model": ModelPriceConfig(input=1, output=2, cache_read=0, cache_write=None)},
    )
