"""The usage screen keeps blocking reads off-loop and recovers from failures."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from textual.widgets import Static

from grove.core.config import GroveConfig
from grove.core.contracts.usage import UsageQuotasView
from grove.core.registry import RepoRegistry
from grove.core.store import JsonWorkspaceStore
from grove.core.usage import UsageService
from grove.tui.app import GroveApp
from grove.tui.screens.usage import UsageScreen, _quota_text, _tokens
from tests.conftest import FakeTmux


class _FailingUsageService:
    def __init__(self) -> None:
        self.attempts = 0
        self.closed = False

    def refresh(self, *, force: bool) -> None:
        self.attempts += 1
        raise RuntimeError("offline")

    def close(self) -> None:
        self.closed = True


class _BlockingFailingUsageService(_FailingUsageService):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.close_finished = threading.Event()
        self.active = False
        self.closed_while_active = False

    def refresh(self, *, force: bool) -> None:
        self.attempts += 1
        self.active = True
        self.started.set()
        self.release.wait(timeout=5)
        self.active = False
        raise RuntimeError("offline")

    def close(self) -> None:
        self.closed_while_active = self.active
        super().close()
        self.close_finished.set()


def _manager(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    cfg = GroveConfig(projects=[str(repo)])
    return RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(tmp_path / "state.json")).get(repo)


@pytest.mark.asyncio
async def test_worker_failure_is_retryable_and_closes_service(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    service = _FailingUsageService()
    app = GroveApp(_manager(tmp_path))
    screen = UsageScreen(service=cast(UsageService, cast(Any, service)))
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(screen)
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "failed" in str(screen.query_one("#usage-body", Static).content).lower()
        assert screen._refreshing is False
        await pilot.press("r")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert service.attempts == 2
        await pilot.press("escape")
        await pilot.pause()
    assert service.closed is True


@pytest.mark.asyncio
async def test_forced_unmount_defers_close_until_thread_worker_finishes(
    fake_tmux: FakeTmux, tmp_path: Path
) -> None:
    del fake_tmux
    service = _BlockingFailingUsageService()
    app = GroveApp(_manager(tmp_path))
    screen = UsageScreen(service=cast(UsageService, cast(Any, service)))
    async with app.run_test(size=(100, 30)) as pilot:
        app.push_screen(screen)
        assert await asyncio.to_thread(service.started.wait, 5)
        app.pop_screen()
        await pilot.pause()
        assert service.closed is False
        service.release.set()
        assert await asyncio.to_thread(service.close_finished.wait, 5)
    assert service.closed_while_active is False


def test_provider_total_is_used_only_when_classes_were_not_measured() -> None:
    tokens = type(
        "Tokens",
        (),
        {
            "fresh_input": None,
            "cache_read": None,
            "cache_creation": None,
            "reasoning": None,
            "output": None,
            "provider_total": 12_345,
        },
    )()
    assert _tokens(tokens) == "12.3k"


def test_empty_quota_selection_names_the_config_surface() -> None:
    assert "usage.quota.profiles" in _quota_text(UsageQuotasView()).plain
