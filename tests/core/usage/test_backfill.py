"""Historical telemetry selection stays explicit and adapter-backed."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.agents.model import AgentMessage, ContentBlock
from grove.core.config import ContentOwner, GroveConfig, TelemetryBackfillConfig, TelemetryConfig
from grove.core.usage._store import UsageStore
from grove.core.usage.backfill import UsageTelemetryBackfill

_NOW = datetime(2026, 8, 9, 20, tzinfo=UTC)


class _Adapter:
    kind = "codex"

    def read_messages(self, cwd: Path, session_id: str) -> tuple[AgentMessage, ...]:
        return (
            AgentMessage(
                role="user",
                timestamp=datetime(2026, 8, 1, 10, tzinfo=UTC),
                content=(ContentBlock(type="text", text="fix it"),),
            ),
            AgentMessage(
                role="assistant",
                timestamp=datetime(2026, 8, 1, 10, 1, tzinfo=UTC),
                content=(ContentBlock(type="text", text="done"),),
            ),
        )


def _store(tmp_path: Path, profile: Path, *, last_event_at: int) -> UsageStore:
    store = UsageStore(tmp_path / "usage.sqlite3")
    with store.write() as conn:
        conn.execute(
            "INSERT INTO sources(source_id, provider, root, label) VALUES(?,?,?,?)",
            ("codex-source", "codex", str(profile), "default"),
        )
        conn.execute(
            "INSERT INTO sessions(session_id, source_id, provider, cwd, last_event_at, "
            "turns, tool_calls, tool_failures, files_changed, duration_confidence, "
            "cost_provenance, models, parser_health) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "session-1",
                "codex-source",
                "codex",
                str(tmp_path),
                last_event_at,
                1,
                0,
                0,
                0,
                "derived",
                "unknown",
                "[]",
                "ok",
            ),
        )
    return store


def _config(profile: Path, *, owner: ContentOwner = "grove") -> GroveConfig:
    return GroveConfig(
        telemetry=TelemetryConfig(
            enabled=True,
            content_owner={"codex": owner},
            backfill=TelemetryBackfillConfig(
                enabled=True,
                profiles={"codex": (str(profile),)},
                content="none",
            ),
        )
    )


def test_dry_run_plans_selected_settled_profile_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "codex"
    profile.mkdir()
    store = _store(
        tmp_path, profile, last_event_at=int(datetime(2026, 8, 1, tzinfo=UTC).timestamp())
    )
    monkeypatch.setattr("grove.core.usage.backfill.get_adapter", lambda kind: _Adapter())

    report = UsageTelemetryBackfill(
        cfg=_config(profile),
        store=store,
        ledger_path=tmp_path / "ledger.sqlite3",
        clock=lambda: _NOW,
    ).run(dry_run=True, limit=10)

    assert report.selected_sessions == 1
    assert report.planned_traces == 1
    assert report.planned_spans == 2
    assert report.ownership_skips == 0
    assert not (tmp_path / "ledger.sqlite3").exists()


def test_external_owner_and_recent_session_are_never_planned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "codex"
    profile.mkdir()
    store = _store(tmp_path, profile, last_event_at=int(_NOW.timestamp()))
    monkeypatch.setattr("grove.core.usage.backfill.get_adapter", lambda kind: _Adapter())

    recent = UsageTelemetryBackfill(cfg=_config(profile), store=store, clock=lambda: _NOW).run(
        dry_run=True, limit=10
    )
    assert recent.selected_sessions == 0

    with store.write() as conn:
        conn.execute(
            "UPDATE sessions SET last_event_at=?",
            (int(datetime(2026, 8, 1, tzinfo=UTC).timestamp()),),
        )
    external = UsageTelemetryBackfill(
        cfg=_config(profile, owner="external"), store=store, clock=lambda: _NOW
    ).run(dry_run=True, limit=10)
    assert external.selected_sessions == 1
    assert external.ownership_skips == 1
    assert external.planned_traces == 0
