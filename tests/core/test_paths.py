"""platformdirs-backed path helpers + project locations."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest import mock

import pytest
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


def test_write_atomic_stages_under_a_name_no_other_writer_can_take(tmp_path: Path) -> None:
    """A FIXED `<file>.tmp` name is what lets concurrent writes corrupt.

    Two writers staging through one shared name interleave into a single file
    that `os.replace` then publishes. Asserting the stage name is unique — and
    that no stage file survives — is what pins the property; asserting only the
    final content would pass against the bug.
    """
    target = tmp_path / "state.json"
    staged: list[str] = []
    real_replace = os.replace

    def _spy(src: object, dst: object) -> None:
        staged.append(Path(str(src)).name)
        real_replace(src, dst)  # type: ignore[arg-type]

    with mock.patch("grove.core.paths.os.replace", _spy):
        paths.write_atomic(target, "first\n")
        paths.write_atomic(target, "second\n")

    assert target.read_text(encoding="utf-8") == "second\n"
    assert len(set(staged)) == 2, staged
    assert staged[0] != "state.json.tmp"
    # Nothing left behind — not the stage files, not a stray `.tmp`.
    assert {p.name for p in tmp_path.iterdir()} == {"state.json"}


def test_write_atomic_never_widens_the_mode_at_the_final_path(tmp_path: Path) -> None:
    """The mode is applied to the stage file, so the window at 0644 never exists."""
    secret = tmp_path / "auth.json"
    paths.write_atomic(secret, "{}\n", mode=0o600)
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    readable = tmp_path / "shared.json"
    paths.write_atomic(readable, "{}\n", mode=0o644)
    assert stat.S_IMODE(readable.stat().st_mode) == 0o644


def test_write_atomic_leaves_the_previous_file_intact_when_the_write_fails(
    tmp_path: Path,
) -> None:
    target = tmp_path / "state.json"
    paths.write_atomic(target, "good\n")
    with (
        mock.patch("grove.core.paths.os.replace", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        paths.write_atomic(target, "bad\n")
    assert target.read_text(encoding="utf-8") == "good\n"
    assert {p.name for p in tmp_path.iterdir()} == {"state.json"}
