"""Durable, credential-safe pricing snapshots from configured model-info endpoints."""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlsplit, urlunsplit

import httpx
from loguru import logger
from pydantic import ValidationError

from grove.core import paths
from grove.core.config import ModelPriceConfig, UsagePricingConfig, UsagePricingSourceConfig

_MAX_RESPONSE_BYTES = 1_000_000

#: Bumped when a snapshot gains a field, so an older cache is re-fetched rather
#: than served with the new key silently absent. A context window read as "not
#: published" because the snapshot predates the field is indistinguishable from
#: one the gateway genuinely does not report, and only the first is repairable.
_SNAPSHOT_VERSION = 2


class PricingCatalog:
    """Merge manual prices with bounded, cacheable source snapshots.

    The cache contains only normalized rates and source identities. Credentials
    are not persisted or included in source outcome logs.
    """

    def __init__(self, cfg: UsagePricingConfig, *, cache_path: Path) -> None:
        self._cfg = cfg
        self._cache_path = cache_path
        self._clock: Callable[[], float] = time.time
        self._environ: Mapping[str, str] = os.environ
        self._transport: httpx.BaseTransport | None = None

    def load(self) -> UsagePricingConfig:
        """Return manual prices plus non-expired cached source rates; never fetch."""
        if not self._cfg.sources:
            return self._cfg
        return self._merged(self._usable_cached(self._read_cache()))

    def context_windows(self) -> dict[str, int]:
        """Cached input-window sizes by model id; never fetches.

        The same snapshot the prices come from, read for a different column: a
        model-info endpoint publishes ``max_input_tokens`` beside the rates, so
        a picker that wants to say how much context a model holds needs no
        second endpoint, credential or cache. A model absent here is one no
        configured source published a window for, which is not a zero.

        Sources that disagree about a model drop it, exactly as prices do — two
        deployments of one id with different windows cannot both be true, and
        picking one by source order would be arbitrary.
        """
        windows: dict[str, list[int]] = defaultdict(list)
        for snapshot in self._usable_cached(self._read_cache()).values():
            for name, window in snapshot.context_windows.items():
                windows[name].append(window)
        return {
            name: sizes[0]
            for name, sizes in windows.items()
            if sizes and all(size == sizes[0] for size in sizes[1:])
        }

    def refresh(self) -> UsagePricingConfig:
        """Reuse fresh source snapshots and fetch only source data past its TTL."""
        if not self._cfg.sources:
            return self._cfg
        # Atomic publication makes unlocked reads safe. Network I/O never holds
        # the cross-process lock used to merge concurrent refresh results.
        cached = self._read_cache()
        current = self._usable_cached(cached)
        fetched: dict[str, _SourceSnapshot] = {}
        for source in self._cfg.sources:
            fingerprint = _fingerprint(source)
            if fingerprint in current:
                logger.debug("pricing source cache hit for token environment {}", source.token_env)
                continue
            snapshot = self._fetch(source)
            if snapshot is not None:
                fetched[fingerprint] = snapshot
        snapshots = {**current, **fetched}
        try:
            with paths.exclusive_lock(self._cache_path):
                latest = self._usable_cached(self._read_cache())
                for key, snapshot in latest.items():
                    if key not in snapshots or snapshot.fetched_at > snapshots[key].fetched_at:
                        snapshots[key] = snapshot
                self._write_cache(snapshots)
        except OSError as exc:
            logger.warning("pricing snapshot cache unavailable: {}", type(exc).__name__)
        return self._merged(snapshots)

    def _read_cache(self) -> dict[str, _SourceSnapshot]:
        try:
            raw = json.loads(self._cache_path.read_text())
        except (OSError, ValueError, TypeError):
            return {}
        if (
            not isinstance(raw, dict)
            or raw.get("version") != _SNAPSHOT_VERSION
            or not isinstance(raw.get("sources"), list)
        ):
            return {}
        snapshots: dict[str, _SourceSnapshot] = {}
        for record in raw["sources"]:
            parsed = _SourceSnapshot.from_json(record)
            if parsed is not None:
                snapshots[parsed.fingerprint] = parsed
        return snapshots

    def _usable_cached(self, cached: Mapping[str, _SourceSnapshot]) -> dict[str, _SourceSnapshot]:
        allowed = {_fingerprint(source) for source in self._cfg.sources}
        now = self._clock()
        return {
            fingerprint: snapshot
            for fingerprint, snapshot in cached.items()
            if fingerprint in allowed
            and 0 <= now - snapshot.fetched_at <= self._cfg.cache_ttl_seconds
        }

    def _fetch(self, source: UsagePricingSourceConfig) -> _SourceSnapshot | None:
        token = self._environ.get(source.token_env, "").strip()
        if not token:
            logger.warning(
                "pricing source unavailable: token environment {} is unset", source.token_env
            )
            return None
        try:
            with (
                httpx.Client(
                    transport=self._transport,
                    timeout=source.timeout_seconds,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    "GET",
                    _model_info_url(source.base_url),
                    headers={"Authorization": f"Bearer {token}", "accept": "application/json"},
                ) as response,
            ):
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise ValueError("response exceeds size limit")
                payload: object = json.loads(body)
            models = _parse_models(payload)
            windows = _parse_context_windows(payload)
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            logger.warning(
                "pricing source unavailable for token environment {}: {}",
                source.token_env,
                type(exc).__name__,
            )
            return None
        logger.info(
            "pricing source refreshed for token environment {}: {} models",
            source.token_env,
            len(models),
        )
        return _SourceSnapshot(_fingerprint(source), self._clock(), models, windows)

    def _write_cache(self, snapshots: Mapping[str, _SourceSnapshot]) -> None:
        document = {
            "version": _SNAPSHOT_VERSION,
            "sources": [snapshot.to_json() for snapshot in snapshots.values()],
        }
        try:
            paths.write_atomic(
                self._cache_path,
                json.dumps(document, separators=(",", ":"), sort_keys=True),
            )
        except OSError as exc:
            logger.warning("pricing snapshot cache was not saved: {}", type(exc).__name__)

    def _merged(self, snapshots: Mapping[str, _SourceSnapshot]) -> UsagePricingConfig:
        by_model: dict[str, list[ModelPriceConfig]] = defaultdict(list)
        for snapshot in snapshots.values():
            for name, price in snapshot.models.items():
                by_model[name].append(price)
        merged = dict(self._cfg.models)
        fetched: set[str] = set()
        for name, prices in by_model.items():
            # Source conflict is not resolved by source ordering. A manual exact
            # entry is deliberate and wins regardless of source disagreement.
            if name in merged or not prices or any(price != prices[0] for price in prices[1:]):
                continue
            merged[name] = prices[0]
            fetched.add(name)
        return self._cfg.with_fetched_models(merged, frozenset(fetched))


@dataclass(slots=True)
class _SourceSnapshot:
    fingerprint: str
    fetched_at: float
    models: dict[str, ModelPriceConfig]
    context_windows: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_json(cls, raw: object) -> _SourceSnapshot | None:
        if not isinstance(raw, dict):
            return None
        fingerprint, fetched_at, models = (
            raw.get("fingerprint"),
            raw.get("fetched_at"),
            raw.get("models"),
        )
        if (
            not isinstance(fingerprint, str)
            or not isinstance(fetched_at, (int, float))
            or not isinstance(models, dict)
        ):
            return None
        parsed: dict[str, ModelPriceConfig] = {}
        try:
            for name, price in models.items():
                if isinstance(name, str) and isinstance(price, dict):
                    parsed[name] = ModelPriceConfig.model_validate(price)
        except ValidationError:
            return None
        raw_windows = raw.get("context_windows")
        windows = (
            {
                name: int(size)
                for name, size in raw_windows.items()
                if isinstance(name, str) and _is_window(size)
            }
            if isinstance(raw_windows, dict)
            else {}
        )
        return cls(fingerprint, float(fetched_at), parsed, windows)

    def to_json(self) -> dict[str, object]:
        return {
            "fingerprint": self.fingerprint,
            "fetched_at": self.fetched_at,
            "models": {name: price.model_dump() for name, price in self.models.items()},
            "context_windows": dict(self.context_windows),
        }


def _is_window(value: object) -> TypeGuard[int]:
    """Whether ``value`` is a usable context-window size.

    ``bool`` is excluded explicitly because it is an ``int`` in Python, and a
    ``True`` that reached a picker would render as a one-token window. A
    ``TypeGuard`` rather than a ``bool`` so the narrowing survives into the
    caller — otherwise every call site needs a cast, which would discard the
    very check this function exists to perform.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _fingerprint(source: UsagePricingSourceConfig) -> str:
    return f"{source.base_url}\n{source.token_env}"


def _model_info_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parts.scheme, parts.netloc, f"{path}/model/info", "", ""))


def _parse_models(payload: object) -> dict[str, ModelPriceConfig]:
    entries: object
    if isinstance(payload, list):
        entries = payload
    elif isinstance(payload, dict):
        entries = payload.get("data")
    else:
        raise ValueError("model-info response is not an object")
    if not isinstance(entries, list):
        raise ValueError("model-info response has no data list")
    result: dict[str, ModelPriceConfig] = {}
    conflicted: set[str] = set()
    for entry in entries:
        parsed = _parse_model(entry)
        if parsed is None:
            continue
        name, price = parsed
        existing = result.get(name)
        if existing is not None and existing != price:
            conflicted.add(name)
        else:
            result[name] = price
    for name in conflicted:
        result.pop(name, None)
    return result


def _parse_context_windows(payload: object) -> dict[str, int]:
    """Input-window sizes by model id, from the same response the rates come from.

    Tolerant in a way ``_parse_models`` is not allowed to be: an unreadable
    entry contributes no window rather than failing the fetch, because a price
    snapshot must not be lost over a display column. A model whose entries
    disagree is dropped, mirroring the price rule.
    """
    entries = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return {}
    result: dict[str, int] = {}
    conflicted: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("model_name")
        info = entry.get("model_info")
        if not isinstance(name, str) or not name or not isinstance(info, dict):
            continue
        window = info.get("max_input_tokens")
        if not _is_window(window):
            continue
        existing = result.get(name)
        if existing is not None and existing != window:
            conflicted.add(name)
        else:
            result[name] = int(window)
    for name in conflicted:
        result.pop(name, None)
    return result


def _parse_model(entry: object) -> tuple[str, ModelPriceConfig] | None:
    if not isinstance(entry, dict):
        return None
    info = entry.get("model_info")
    pricing = entry.get("pricing")
    sources = [entry]
    if isinstance(info, dict):
        sources.append(info)
    if isinstance(pricing, dict):
        sources.append(pricing)
    name = next(
        (
            source[key]
            for source in sources
            for key in ("model_name", "model", "id")
            if isinstance(source.get(key), str) and source[key]
        ),
        None,
    )
    if not isinstance(name, str):
        return None
    values: dict[str, float | None] = {}
    for target, per_token, per_million in (
        ("input", "input_cost_per_token", "input_cost_per_million"),
        ("output", "output_cost_per_token", "output_cost_per_million"),
        ("cache_read", "cache_read_input_token_cost", "cache_read_cost_per_million"),
        ("cache_write", "cache_creation_input_token_cost", "cache_write_cost_per_million"),
    ):
        value = _rate(sources, per_token, per_million)
        values[target] = value
    return name, ModelPriceConfig.model_validate(values)


def _rate(sources: list[dict[str, Any]], per_token: str, per_million: str) -> float | None:
    for source in sources:
        if per_million in source:
            value = source[per_million]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
            return None
        if per_token in source:
            value = source[per_token]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value) * 1_000_000
            return None
    return None
