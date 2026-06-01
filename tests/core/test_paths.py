"""platformdirs-backed path helpers + project locations."""

from __future__ import annotations

from pathlib import Path

from grove.core import paths


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
