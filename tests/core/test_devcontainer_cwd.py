"""Every devcontainer CLI spawn pins its working directory.

Not hygiene. The CLI re-spawns `docker` against `opts.cwd || <its own cwd>`,
and it inherits ours — so an unpinned spawn binds a multi-minute provision to
whatever directory the caller happened to stand in. Node reports a spawn whose
`cwd` no longer exists as `spawn <command> ENOENT`, naming the COMMAND, so the
failure reads as "docker is not installed" and sends the reader to PATH.
"""

from __future__ import annotations

import contextlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from grove.core.devcontainer import DevcontainerCli


class _Recorder:
    """Captures the kwargs of every spawn, and answers plausibly enough to get past parsing."""

    def __init__(self) -> None:
        self.cwds: list[Any] = []

    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.cwds.append(kwargs.get("cwd", "MISSING"))
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    def popen(self, argv: list[str], **kwargs: Any) -> Any:
        self.cwds.append(kwargs.get("cwd", "MISSING"))
        raise subprocess.SubprocessError("stop here — the spawn kwargs are what we came for")


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(subprocess, "run", rec.run)
    monkeypatch.setattr(subprocess, "Popen", rec.popen)
    return rec


def _ignoring_the_result(call: Any) -> None:
    """Run *call* for its spawn kwargs alone.

    The recorder answers with a stub payload, so a verb may return or may fail
    parsing depending on the shape it expects — neither is what these tests are
    about, and pinning it either way would make them break on an unrelated
    parsing change.
    """
    with contextlib.suppress(Exception):
        call()


def test_read_configuration_pins_cwd_to_the_workspace(recorder: _Recorder, tmp_path: Path) -> None:
    _ignoring_the_result(lambda: DevcontainerCli().read_configuration(tmp_path))
    assert recorder.cwds == [tmp_path]


def test_up_pins_cwd_to_the_workspace(recorder: _Recorder, tmp_path: Path) -> None:
    _ignoring_the_result(lambda: DevcontainerCli().up(tmp_path))
    assert recorder.cwds == [tmp_path]


def test_build_pins_cwd_to_the_workspace(recorder: _Recorder, tmp_path: Path) -> None:
    _ignoring_the_result(lambda: DevcontainerCli().build(tmp_path))
    assert recorder.cwds == [tmp_path]


def test_exec_pins_cwd_to_the_workspace(recorder: _Recorder, tmp_path: Path) -> None:
    DevcontainerCli().exec(tmp_path, ["true"])
    assert recorder.cwds == [tmp_path]


def test_no_spawn_inherits_the_callers_directory(recorder: _Recorder, tmp_path: Path) -> None:
    """The census. `MISSING` here means a spawn went out unpinned — add the
    kwarg rather than relaxing this."""
    cli = DevcontainerCli()
    for call in (
        lambda: cli.exec(tmp_path, ["true"]),
        lambda: cli.read_configuration(tmp_path),
        lambda: cli.up(tmp_path),
        lambda: cli.build(tmp_path),
    ):
        _ignoring_the_result(call)
    assert "MISSING" not in recorder.cwds
    assert recorder.cwds == [tmp_path] * 4
