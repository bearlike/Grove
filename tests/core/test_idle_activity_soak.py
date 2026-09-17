"""Opt-in ten-minute idle budget over the real native filesystem/runtime owners."""

from __future__ import annotations

import asyncio
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grove.core.activity import ActivityService
from grove.core.activity_runtime import ActivityRuntime
from grove.core.activity_sources import ActivitySources
from grove.core.admission import AdmissionLimits
from grove.core.config import GroveConfig
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState, WorkspaceStatus


@pytest.mark.integration
@pytest.mark.asyncio
async def test_idle_projection_ten_minute_budget(tmp_path: Path) -> None:
    """Run on CI: quiet native sources must not manufacture refresh work."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-qm",
            "baseline",
        ],
        check=True,
    )
    now = datetime.now(UTC)
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    store.save(
        WorkspaceState(
            id="idle-budget",
            title="Idle budget",
            repo_root=str(root),
            branch="main",
            base_branch="main",
            worktree_path=str(root),
            tmux_session="unused",
            agent_name="shell",
            agent_kind="generic",
            status=WorkspaceStatus.PAUSED,
            created_at=now,
            updated_at=now,
        )
    )
    service = ActivityService(registry=RepoRegistry(cfg=GroveConfig(), store=store))
    runtime = ActivityRuntime(service, limits=AdmissionLimits())
    sources = ActivitySources(runtime, store)
    events: list[str] = []
    service.subscribe(lambda delta: events.append(delta.kind))
    await runtime.start()
    await runtime.wait_ready()
    try:
        await sources.start()
        await asyncio.sleep(2)  # Let native bootstrap publication settle.
        events.clear()
        initial_seq = service.snapshot_with_cursor()[1]
        initial_wakeups = runtime._inbox.stats().wakeups
        cpu_start, wall_start = time.process_time(), time.monotonic()
        await asyncio.sleep(600)
        elapsed = time.monotonic() - wall_start
        cpu = time.process_time() - cpu_start
        print(f"idle wall={elapsed:.3f}s cpu={cpu:.3f}s one_core_percent={100 * cpu / elapsed:.4f}")
        assert runtime._inbox.stats().wakeups == initial_wakeups
        assert service.snapshot_with_cursor()[1] == initial_seq
        assert events == []
        assert cpu / elapsed < 0.05
        # The zero-work result must not come from disabling observation.
        (root / "changed.txt").write_text("external change\n")
        async with asyncio.timeout(5):
            while service.snapshot().projects[0].workspaces[0].dirty_files != 1:
                await asyncio.sleep(0.01)
    finally:
        await sources.close()
        await runtime.close()
