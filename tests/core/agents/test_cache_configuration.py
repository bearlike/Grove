"""Process-wide cache budgets remain adjustable without capping read results."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from grove.core.agents import configure_transcript_caches
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.codex import CodexAdapter
from grove.core.config import GroveConfig, TranscriptCacheConfig
from grove.core.store import JsonWorkspaceStore
from grove.daemon.app import build_app


@pytest.mark.parametrize("field", TranscriptCacheConfig.model_fields)
@pytest.mark.parametrize("value", [0, -1])
def test_cache_budgets_refuse_nonpositive_values(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        TranscriptCacheConfig.model_validate({field: value})


def test_configured_budgets_reach_both_adapter_owners(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[TranscriptCacheConfig] = []
    for adapter in (ClaudeCodeAdapter, CodexAdapter):
        monkeypatch.setattr(adapter, "configure_caches", staticmethod(received.append))
    cfg = GroveConfig.model_validate(
        {"transcript_cache": {"max_retained_bytes": 1024, "memo_max_bytes": 2048}}
    )
    configure_transcript_caches(cfg.transcript_cache)
    assert received == [cfg.transcript_cache, cfg.transcript_cache]


def test_global_daemon_configuration_reaches_cache_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[TranscriptCacheConfig] = []
    monkeypatch.setattr("grove.daemon.app.configure_transcript_caches", received.append)
    cfg = GroveConfig.model_validate(
        {"transcript_cache": {"max_retained_bytes": 1024, "max_source_bytes": None}}
    )
    build_app(cfg=cfg, store=JsonWorkspaceStore(path=tmp_path / "state.json"))
    assert received == [cfg.transcript_cache]
