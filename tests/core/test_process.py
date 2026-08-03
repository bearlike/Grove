"""Detached-process side effects + the host-wide liveness scan (`core/process.py`).

`list_agent_runtimes` is exercised against a FAKE `/proc` tree (via the
injectable `proc_root` test seam), never a monkeypatched internal — matching
on-host reality: a real running Claude Code CLI's `/proc/<pid>/comm` is
literally ``claude``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from grove.core.process import list_agent_runtimes


def _make_proc_entry(
    proc_root: Path, pid: int, *, comm: str | None = None, cwd: Path | None = None
) -> Path:
    entry = proc_root / str(pid)
    entry.mkdir(parents=True)
    if comm is not None:
        (entry / "comm").write_text(f"{comm}\n", encoding="utf-8")
    if cwd is not None:
        os.symlink(cwd, entry / "cwd")
    return entry


def test_returns_empty_on_non_linux(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    _make_proc_entry(tmp_path, 111, comm="claude", cwd=tmp_path)
    assert list_agent_runtimes(proc_root=tmp_path) == ()


def test_degrades_to_empty_when_proc_root_is_missing(tmp_path: Path) -> None:
    assert list_agent_runtimes(proc_root=tmp_path / "does-not-exist") == ()


def test_recognizes_known_binaries_and_skips_the_rest(tmp_path: Path) -> None:
    """`claude` -> `claude_code`, `codex` -> `codex`; an unrecognized comm
    (a bare shell, an unrelated process) and a non-numeric `/proc` entry
    (e.g. `self`) are both skipped, never raised on."""
    claude_cwd = tmp_path / "claude-work"
    codex_cwd = tmp_path / "codex-work"
    claude_cwd.mkdir()
    codex_cwd.mkdir()
    _make_proc_entry(tmp_path, 100, comm="claude", cwd=claude_cwd)
    _make_proc_entry(tmp_path, 200, comm="codex", cwd=codex_cwd)
    _make_proc_entry(tmp_path, 300, comm="bash", cwd=tmp_path)  # unrecognized binary
    (tmp_path / "self").mkdir()  # non-numeric proc entry, must never be treated as a pid

    runtimes = list_agent_runtimes(proc_root=tmp_path)

    assert {(rt.pid, rt.kind, rt.cwd) for rt in runtimes} == {
        (100, "claude_code", claude_cwd),
        (200, "codex", codex_cwd),
    }


def test_a_vanished_or_unreadable_entry_is_skipped_not_raised(tmp_path: Path) -> None:
    """A recognized comm with no readable `cwd` symlink (process exited
    mid-scan, or a permissions edge case) degrades to a skip — the same
    best-effort contract as every other read in this codebase."""
    _make_proc_entry(tmp_path, 400, comm="claude", cwd=None)  # no cwd symlink at all
    _make_proc_entry(tmp_path, 500, comm="claude", cwd=tmp_path)

    runtimes = list_agent_runtimes(proc_root=tmp_path)

    assert [rt.pid for rt in runtimes] == [500]
