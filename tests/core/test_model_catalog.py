"""Model picker rows enrich, but never replace, the configured catalog."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from grove.core.config import AgentSpec, GroveConfig, ModelPriceConfig, UsagePricingConfig
from grove.core.contracts.agents import ModelOptionView
from grove.core.model_catalog import model_options
from grove.core.usage.pricing_sources import PricingCatalog, _is_window, _SourceSnapshot


def test_model_options_keep_catalog_order_and_honestly_enrich_each_row() -> None:
    """Names and windows decorate configured ids without guessing either absence."""
    cfg = GroveConfig.model_validate({"models": {"display_names": {"named": "Named model"}}})
    spec = AgentSpec(
        name="picker",
        command="picker",
        models=("unlabelled", "named", "no-published-window"),
    )

    options = model_options(
        spec,
        cfg=cfg,
        context_windows={"unlabelled": 200_000, "named": 128_000},
    )

    assert options == (
        ModelOptionView(id="unlabelled", name=None, context_window=200_000),
        ModelOptionView(id="named", name="Named model", context_window=128_000),
        ModelOptionView(id="no-published-window", name=None, context_window=None),
    )


def test_model_options_do_not_invent_a_row_from_pricing_metadata() -> None:
    cfg = GroveConfig()
    spec = AgentSpec(name="picker", command="picker", models=("catalog-model",))

    options = model_options(
        spec,
        cfg=cfg,
        context_windows={"catalog-model": 32_000, "priced-only": 1_000_000},
    )

    assert options == (ModelOptionView(id="catalog-model", context_window=32_000),)


def test_context_windows_drop_conflicts_and_keep_prices_despite_bad_windows(tmp_path: Path) -> None:
    """A display-only malformed field cannot discard the price snapshot beside it."""
    catalog = _catalog(
        _pricing_config(),
        cache_path=tmp_path / "prices.json",
        handler=lambda request: httpx.Response(
            200,
            json={
                "data": [
                    _entry("published", price=0.000001, window=200_000),
                    _entry("bad-window", price=0.000002, window=True),
                    _entry("conflicted", price=0.000003, window=100_000),
                    _entry("conflicted", price=0.000003, window=200_000),
                ]
            },
        ),
    )

    prices = catalog.refresh()

    assert set(prices.models) == {"published", "bad-window", "conflicted"}
    assert prices.models["bad-window"].input == 2.0
    assert catalog.context_windows() == {"published": 200_000}


def test_old_snapshot_version_is_refetched_instead_of_serving_missing_windows(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "prices.json"
    source = _pricing_config().sources[0]
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "fingerprint": f"{source.base_url}\n{source.token_env}",
                        "fetched_at": 100,
                        "models": {"stale": ModelPriceConfig(input=1).model_dump()},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    requests: list[httpx.Request] = []
    catalog = _catalog(
        _pricing_config(),
        cache_path=cache_path,
        clock=lambda: 100,
        handler=lambda request: (
            requests.append(request)
            or httpx.Response(200, json={"data": [_entry("fresh", price=0.000002, window=256_000)]})
        ),
    )

    refreshed = catalog.refresh()

    assert len(requests) == 1
    assert set(refreshed.models) == {"fresh"}
    assert catalog.context_windows() == {"fresh": 256_000}
    assert json.loads(cache_path.read_text(encoding="utf-8"))["version"] == 2


def test_context_windows_drop_a_model_when_cached_sources_disagree(tmp_path: Path) -> None:
    cfg = UsagePricingConfig.model_validate(
        {
            "sources": [
                {"base_url": "https://one.example.test", "token_env": "ONE"},
                {"base_url": "https://two.example.test", "token_env": "TWO"},
            ]
        }
    )
    catalog = PricingCatalog(cfg, cache_path=tmp_path / "prices.json")
    catalog._clock = lambda: 100
    catalog._write_cache(
        {
            "https://one.example.test\nONE": _SourceSnapshot(
                "https://one.example.test\nONE",
                100,
                {"shared": ModelPriceConfig(input=1)},
                {"shared": 128_000},
            ),
            "https://two.example.test\nTWO": _SourceSnapshot(
                "https://two.example.test\nTWO",
                100,
                {"shared": ModelPriceConfig(input=1)},
                {"shared": 256_000},
            ),
        }
    )

    assert catalog.context_windows() == {}


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "200000", None])
def test_is_window_rejects_nonpositive_booleans_and_nonintegers(value: object) -> None:
    assert not _is_window(value)


def test_is_window_accepts_a_positive_integer() -> None:
    assert _is_window(200_000)


def _pricing_config() -> UsagePricingConfig:
    return UsagePricingConfig.model_validate(
        {
            "sources": [
                {
                    "base_url": "https://prices.example.test",
                    "token_env": "PRICE_TOKEN",
                }
            ]
        }
    )


def _entry(model: str, *, price: float, window: object) -> dict[str, object]:
    return {
        "model_name": model,
        "input_cost_per_token": price,
        "model_info": {"max_input_tokens": window},
    }


def _catalog(
    cfg: UsagePricingConfig,
    *,
    cache_path: Path,
    handler: Callable[[httpx.Request], httpx.Response],
    clock: Callable[[], float] = lambda: 0,
) -> PricingCatalog:
    catalog = PricingCatalog(cfg, cache_path=cache_path)
    catalog._clock = clock
    catalog._environ = {"PRICE_TOKEN": "test-token"}
    catalog._transport = httpx.MockTransport(handler)
    return catalog
