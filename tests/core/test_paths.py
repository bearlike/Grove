"""platformdirs-backed path helpers + project locations."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from grove.core import paths


def test_ensure_dir_creates_once_and_logs_the_first_creation(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "state"
    messages: list[str] = []
    sink_id = logger.add(messages.append, level="INFO")
    try:
        assert paths.ensure_dir(target) == target
        assert target.is_dir()
        # Second call is a silent no-op — no duplicate "initialized" noise.
        assert paths.ensure_dir(target) == target
    finally:
        logger.remove(sink_id)
    initialized = [m for m in messages if "initialized" in m]
    assert len(initialized) == 1
    assert str(target) in initialized[0]


def test_user_config_path_is_writable_format(tmp_state_dir: Path) -> None:
    p = paths.user_config_path()
    assert p.name == "config.json"
    # tmp_state_dir redirected the helpers — assert it landed in our tmpdir
    assert tmp_state_dir.parent in p.parents


def test_project_config_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    assert paths.project_config_path(repo) == repo / ".grove" / "config.json"
    assert paths.project_local_config_path(repo) == repo / ".grove" / "config.local.json"
    assert paths.project_grove_dir(repo) == repo / ".grove"
