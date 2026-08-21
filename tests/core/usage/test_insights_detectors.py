"""Focused acceptance tests for deterministic usage insight detectors."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grove.core.config import GroveConfig
from grove.core.contracts.usage import UsageFilters, UsageFindingView
from grove.core.usage._store import UsageStore
from grove.core.usage.insights import (
    ConcentrationDetector,
    EditChurnDetector,
    InsightDetector,
    InsightEngine,
    RecurringToolFailureDetector,
    RetryLoopDetector,
    SlowOperationDetector,
    TokenStructureDetector,
)


def _epoch(offset: int = 0) -> int:
    return int(datetime(2026, 8, 9, 12, tzinfo=UTC).timestamp()) + offset


def _config(**insights: Any) -> GroveConfig:
    return GroveConfig.model_validate({"usage": {"insights": insights}})


def _store(tmp_path: Path) -> UsageStore:
    return UsageStore(tmp_path / "usage.db")


def _insert_session(
    store: UsageStore,
    session_id: str,
    *,
    project: str = "/repo/a",
    account: str | None = "account-a",
    provider_total: int | None = None,
    active_ms: int | None = None,
    fresh_input: int | None = None,
    cache_read: int | None = None,
    cache_creation: int | None = None,
    models: tuple[str, ...] = ("model-a",),
) -> None:
    with store.write() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sources(source_id, provider, root, label) "
            "VALUES('source', 'claude_code', '/profile', 'profile')"
        )
        conn.execute(
            "INSERT INTO sessions("
            "session_id, source_id, provider, cwd, project, account_id, started_at, "
            "last_event_at, active_ms, fresh_input, cache_read, cache_creation, "
            "provider_total, models, cost_provenance, parser_health"
            ") VALUES(?, 'source', 'claude_code', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "'unknown', 'ok')",
            (
                session_id,
                project,
                project,
                account,
                _epoch(),
                _epoch(1000),
                active_ms,
                fresh_input,
                cache_read,
                cache_creation,
                provider_total,
                json.dumps(models),
            ),
        )


def _insert_events(store: UsageStore, session_id: str, rows: list[dict[str, Any]]) -> None:
    with store.write() as conn:
        conn.executemany(
            "INSERT INTO usage_events("
            "session_id, source_id, seq, ts, kind, model, tool_name, target, "
            "failure_category, is_error, duration_ms"
            ") VALUES(?, 'source', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    session_id,
                    index,
                    row.get("ts", _epoch(index)),
                    row["kind"],
                    row.get("model"),
                    row.get("tool_name"),
                    row.get("target"),
                    row.get("failure_category"),
                    row.get("is_error", 0),
                    row.get("duration_ms"),
                )
                for index, row in enumerate(rows)
            ],
        )


def test_recurring_failures_are_scoped_and_include_success_rate(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_session(store, "scoped", project="/repo/a", account="account-a")
    _insert_session(store, "other-project", project="/repo/b", account="account-a")
    _insert_events(
        store,
        "scoped",
        [
            *[
                {
                    "kind": "tool_result",
                    "tool_name": "Read",
                    "failure_category": "not_found",
                    "is_error": 1,
                }
                for _ in range(3)
            ],
            *[{"kind": "tool_result", "tool_name": "Read"} for _ in range(7)],
        ],
    )
    _insert_events(
        store,
        "other-project",
        [
            {
                "kind": "tool_result",
                "tool_name": "Read",
                "failure_category": "not_found",
                "is_error": 1,
            }
            for _ in range(2)
        ],
    )

    findings = RecurringToolFailureDetector().detect(store, UsageFilters(), _config())

    assert len(findings) == 1
    assert findings[0].count == 3
    assert findings[0].title == "Recurring failures in `Read`"
    assert (
        findings[0].detail == "3 of 10 observed results failed (70% success); category: not_found."
    )
    assert findings[0].evidence_filters.project == "/repo/a"
    assert findings[0].evidence_filters.account == "account-a"
    assert findings[0].evidence_filters.provider == "claude_code"
    store.close()


def test_retry_loop_requires_recurrence_inside_configured_window(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_session(store, "loop")
    _insert_session(store, "spaced")
    failure = {
        "kind": "tool_result",
        "tool_name": "Bash",
        "target": "tests",
        "failure_category": "exit_nonzero",
        "is_error": 1,
    }
    _insert_events(
        store,
        "loop",
        [{**failure, "ts": _epoch(offset)} for offset in (0, 10, 20)],
    )
    _insert_events(
        store,
        "spaced",
        [{**failure, "ts": _epoch(offset)} for offset in (0, 40, 80)],
    )

    findings = RetryLoopDetector().detect(
        store,
        UsageFilters(),
        _config(min_occurrences=3, retry_window_seconds=30),
    )

    assert [finding.session_ids for finding in findings] == [("loop",)]
    store.close()


def test_edit_churn_is_per_target_and_per_session(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_session(store, "churn")
    _insert_session(store, "spread")
    _insert_events(
        store,
        "churn",
        [{"kind": "file_edit", "target": "src/a.py"} for _ in range(4)],
    )
    _insert_events(
        store,
        "spread",
        [
            {"kind": "file_edit", "target": "src/a.py"},
            {"kind": "file_edit", "target": "src/b.py"},
            {"kind": "file_edit", "target": "src/c.py"},
            {"kind": "file_edit", "target": "src/d.py"},
        ],
    )

    findings = EditChurnDetector().detect(store, UsageFilters(), _config(edit_churn_edits=4))

    assert len(findings) == 1
    assert findings[0].subject == "src/a.py"
    assert findings[0].session_ids == ("churn",)
    store.close()


def test_slow_operation_uses_high_p95_not_one_outlier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _insert_session(store, "slow", project="/repo/slow")
    _insert_session(store, "outlier", project="/repo/outlier")
    _insert_events(
        store,
        "slow",
        [
            {
                "kind": "tool_result",
                "tool_name": "Bash",
                "duration_ms": duration,
            }
            for duration in [*[10] * 18, 200, 300]
        ],
    )
    _insert_events(
        store,
        "outlier",
        [
            {
                "kind": "tool_result",
                "tool_name": "Bash",
                "duration_ms": duration,
            }
            for duration in [*[10] * 19, 1000]
        ],
    )

    findings = SlowOperationDetector().detect(
        store,
        UsageFilters(),
        _config(min_occurrences=3, slow_operation_ms=100),
    )

    assert len(findings) == 1
    assert findings[0].evidence_filters.project == "/repo/slow"
    assert findings[0].count == 2
    assert findings[0].detail == "Measured p95 was 200 ms across 20 operations."
    store.close()


def test_token_structure_requires_measured_cache_reads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index in range(3):
        _insert_session(
            store,
            f"dominant-{index}",
            project="/repo/dominant",
            fresh_input=80,
            cache_read=20,
        )
        _insert_session(
            store,
            f"balanced-{index}",
            project="/repo/balanced",
            fresh_input=20,
            cache_read=80,
        )
        _insert_session(
            store,
            f"unknown-{index}",
            project="/repo/unknown",
            fresh_input=100,
            cache_read=None,
        )

    findings = TokenStructureDetector().detect(store, UsageFilters(), _config(min_occurrences=3))

    assert [finding.subject for finding in findings] == ["/repo/dominant"]
    assert findings[0].evidence_filters.project == "/repo/dominant"
    assert findings[0].detail == (
        "Measured fresh/cache-write input was 80% of the compared input tokens."
    )
    store.close()


def test_concentration_falls_back_to_active_time_and_excludes_multi_model_rows(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for index in range(4):
        _insert_session(
            store,
            f"dominant-{index}",
            project="/repo/dominant",
            account="account-a",
            active_ms=20,
            models=("model-a",),
        )
    _insert_session(
        store,
        "minority",
        project="/repo/minority",
        account="account-b",
        active_ms=20,
        models=("model-b",),
    )
    _insert_session(
        store,
        "switched",
        project="/repo/switched",
        account="account-c",
        active_ms=1000,
        models=("model-a", "model-b"),
    )

    findings = ConcentrationDetector().detect(
        store, UsageFilters(), _config(concentration_share=0.75)
    )
    model_findings = [finding for finding in findings if "model " in finding.title]

    assert [finding.subject for finding in model_findings] == ["model-a"]
    assert model_findings[0].detail == "80% of measured active time is in this model."
    assert model_findings[0].evidence_filters.model == "model-a"
    assert all(finding.subject not in {"model-b", "model-a,model-b"} for finding in model_findings)
    store.close()


class _BrokenDetector(InsightDetector):
    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        raise RuntimeError("malformed evidence")


class _StaticDetector(InsightDetector):
    def detect(
        self, store: UsageStore, filters: UsageFilters, cfg: GroveConfig
    ) -> tuple[UsageFindingView, ...]:
        return (
            UsageFindingView(
                kind="edit_churn",
                title="High impact, one occurrence",
                count=1,
                impact=0.9,
                confidence=1.0,
            ),
            UsageFindingView(
                kind="retry_loop",
                title="Recurring moderate impact",
                count=4,
                impact=0.3,
                confidence=1.0,
            ),
        )


def test_engine_isolates_detector_exceptions_and_ranks_with_recurrence(tmp_path: Path) -> None:
    store = _store(tmp_path)
    engine = InsightEngine(
        store=store,
        cfg=_config(),
        detectors=(_BrokenDetector(), _StaticDetector()),
    )

    findings = engine.findings(UsageFilters()).findings

    assert [finding.title for finding in findings] == [
        "Recurring moderate impact",
        "High impact, one occurrence",
    ]
    store.close()
