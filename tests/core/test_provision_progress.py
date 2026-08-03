"""`ProvisionProgress` — the read a waiting user's surface polls.

Pinned here rather than beside the manager because the type is pure: it takes a
record and a log on disk and answers, with no engine, no docker and no clock
Grove controls.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core.workspace import (
    ProvisionProgress,
    ProvisionStatus,
    WorkspaceState,
    WorkspaceStatus,
)


def _state(tmp_path: Path, **over: object) -> WorkspaceState:
    now = datetime.now(UTC).isoformat()
    base: dict[str, object] = {
        "id": "w1",
        "title": "t",
        "repo_root": str(tmp_path),
        "branch": "b",
        "base_branch": "main",
        "worktree_path": str(tmp_path),
        "tmux_session": "s",
        "agent_name": "a",
        "status": WorkspaceStatus.RUNNING,
        "created_at": now,
        "updated_at": now,
    }
    base.update(over)
    return WorkspaceState(**base)  # type: ignore[arg-type]


def test_a_finished_provision_reports_its_duration_not_a_growing_clock(tmp_path: Path) -> None:
    """The regression a real `grove create` caught and no unit test had.

    `provision_started_at` outlives the provision, so an elapsed derived from
    the clock alone reports an 11-second provision as five minutes and climbing
    purely because nobody looked at the workspace in the meantime.
    """
    log = tmp_path / "p.log"
    log.write_text("[00:00:01] building\n", encoding="utf-8")
    began = datetime.now(UTC) - timedelta(minutes=5)
    state = _state(
        tmp_path,
        provision_status=ProvisionStatus.OK,
        provision_started_at=began.isoformat(),
        provision_duration_ms=11_681,
        provision_log_path=str(log),
    )

    assert ProvisionProgress.read(state).elapsed_ms == 11_681


def test_an_in_flight_provision_counts_up_from_its_start_stamp(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("[00:00:01] building\n", encoding="utf-8")
    state = _state(
        tmp_path,
        provision_status=ProvisionStatus.PROVISIONING,
        provision_started_at=(datetime.now(UTC) - timedelta(seconds=90)).isoformat(),
        provision_duration_ms=None,
        provision_log_path=str(log),
    )

    elapsed = ProvisionProgress.read(state).elapsed_ms
    assert elapsed is not None
    assert 88_000 <= elapsed <= 95_000


def test_the_headline_is_the_last_non_blank_line_and_blanks_are_dropped(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("[0] first\n\n[1] pulling image\n   \n", encoding="utf-8")
    state = _state(tmp_path, provision_log_path=str(log))

    progress = ProvisionProgress.read(state)
    assert progress.headline == "[1] pulling image"
    assert progress.lines == ("[0] first", "[1] pulling image")


def test_the_tail_is_bounded(tmp_path: Path) -> None:
    log = tmp_path / "p.log"
    log.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
    state = _state(tmp_path, provision_log_path=str(log))

    progress = ProvisionProgress.read(state)
    assert len(progress.lines) == ProvisionProgress.TAIL_LINES
    assert progress.headline == "line 499"


def test_a_missing_or_unset_log_degrades_instead_of_raising(tmp_path: Path) -> None:
    """Best-effort by contract: the caller is a status surface mid-render."""
    for path in (None, str(tmp_path / "gone.log")):
        progress = ProvisionProgress.read(_state(tmp_path, provision_log_path=path))
        assert progress.lines == ()
        assert progress.headline == ""


def test_no_start_stamp_is_an_absent_elapsed_not_a_zero(tmp_path: Path) -> None:
    """A record written before the field existed. Zero would read as
    'it just started', which is a claim; None is the honest answer."""
    assert ProvisionProgress.read(_state(tmp_path)).elapsed_ms is None
