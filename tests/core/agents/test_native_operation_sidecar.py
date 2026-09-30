"""A native step crosses the spool as a SET-OR-CLEAR fact, not an accumulated one.

Every other native fact merges "stated wins, unstated keeps": a cost drop never
blanks an exit code. A step is different — its end is stated as ``null`` — so
the fold must read key PRESENCE for it. These pin both halves against the real
drain, the one seam every consumer reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from grove.core import paths
from grove.core.agents.hook import ClaudeHook
from grove.core.agents.model import NativeOperation
from grove.core.agents.native_owner import AskRecorder


def _fold(sidecars: Path) -> None:
    ClaudeHook.drain(sidecar_dir=sidecars, spool_dir=paths.agent_hook_spool_dir(sidecars))


def test_a_step_is_cleared_by_an_explicit_null_and_kept_by_its_absence(tmp_path: Path) -> None:
    sidecars = tmp_path / "sidecars"
    asks = AskRecorder(paths.agent_hook_spool_dir(sidecars))
    asks.bind("s")
    started = datetime(2026, 9, 23, 19, 4, 27, tzinfo=UTC)

    asks.facts(operation=NativeOperation(kind="compacting", started_at=started))
    _fold(sidecars)
    record = ClaudeHook.read("s", sidecar_dir=sidecars)
    assert record is not None and record.native is not None
    assert record.native.operation == NativeOperation(kind="compacting", started_at=started)

    # A cost update says nothing about the step: it must survive it.
    asks.facts(cost_usd=0.5)
    _fold(sidecars)
    record = ClaudeHook.read("s", sidecar_dir=sidecars)
    assert record is not None and record.native is not None
    assert record.native.operation is not None
    assert record.native.cost_usd == 0.5

    # The step's end is a stated null, and it must clear.
    asks.facts(operation=None, compact_error="summarization produced empty response")
    _fold(sidecars)
    record = ClaudeHook.read("s", sidecar_dir=sidecars)
    assert record is not None and record.native is not None
    assert record.native.operation is None
    assert record.native.compact_error == "summarization produced empty response"
    assert record.native.cost_usd == 0.5


def test_the_step_round_trips_through_a_hook_event_write(tmp_path: Path) -> None:
    """Hook events carry `native` forward by value; the step must survive one."""
    sidecars = tmp_path / "sidecars"
    asks = AskRecorder(paths.agent_hook_spool_dir(sidecars))
    asks.bind("s")
    retry_at = datetime(2026, 9, 23, 19, 5, tzinfo=UTC)
    asks.facts(
        operation=NativeOperation(
            kind="retrying",
            started_at=datetime(2026, 9, 23, 19, 4, 52, tzinfo=UTC),
            attempt=2,
            max_attempts=10,
            retry_at=retry_at,
            detail="overloaded (529)",
        )
    )
    _fold(sidecars)
    ClaudeHook.record_event(
        {"hook_event_name": "PreToolUse", "session_id": "s"},
        sidecar_dir=sidecars,
        tmux_pane=None,
        now=datetime.now(UTC),
    )
    record = ClaudeHook.read("s", sidecar_dir=sidecars)
    assert record is not None and record.native is not None
    assert record.native.operation is not None
    assert record.native.operation.retry_at == retry_at
    assert record.native.operation.detail == "overloaded (529)"
