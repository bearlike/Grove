from __future__ import annotations

import json
from pathlib import Path

import pytest

from grove.core.agents.session_registry import NativeClaudeSession, NativeSessionRegistry


def _record(*, pid: int, proc_start: str | None = "42", **extra: object) -> dict[str, object]:
    return {
        "pid": pid,
        "sessionId": "session-1",
        "cwd": "/worktree",
        "kind": "interactive",
        "status": "busy",
        "procStart": proc_start,
        "tmux": "grove-work:@1.%3",
        **extra,
    }


def _write(base: Path, name: str, data: dict[str, object]) -> Path:
    path = base / "sessions" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_registry_cache() -> None:
    NativeSessionRegistry.clear_cache()
    yield
    NativeSessionRegistry.clear_cache()


def test_scan_returns_live_interactive_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = tmp_path / "claude"
    _write(base, "12.json", _record(pid=12))
    monkeypatch.setattr(
        NativeSessionRegistry,
        "is_live",
        staticmethod(lambda record: record.pid == 12),
    )

    assert NativeSessionRegistry.scan([base]) == (
        NativeSessionRegistry.for_session("session-1", [base]),
    )
    record = NativeSessionRegistry.for_session("session-1", [base])
    assert record is not None
    assert record.status == "busy"
    assert record.tmux == "grove-work:@1.%3"


def test_scan_ignores_stale_pid_record(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    base = tmp_path / "claude"
    _write(base, "12.json", _record(pid=12))
    monkeypatch.setattr(NativeSessionRegistry, "is_live", staticmethod(lambda _record: False))

    assert NativeSessionRegistry.scan([base]) == ()


def test_scan_ignores_noninteractive_or_malformed_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = tmp_path / "claude"
    _write(base, "12.json", _record(pid=12, kind="headless"))
    _write(base, "13.json", _record(pid=13, status="blocked"))
    _write(base, "14.json", _record(pid=14, sessionId=""))
    monkeypatch.setattr(NativeSessionRegistry, "is_live", staticmethod(lambda _record: True))

    assert NativeSessionRegistry.scan([base]) == ()


def test_scan_memoizes_unchanged_json_but_checks_liveness_each_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    base = tmp_path / "claude"
    record_path = _write(base, "12.json", _record(pid=12))
    reads = 0
    original = Path.read_text

    def counted_read_text(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal reads
        if path == record_path:
            reads += 1
        return original(path, *args, **kwargs)

    live = iter((True, False))
    monkeypatch.setattr(Path, "read_text", counted_read_text)
    monkeypatch.setattr(NativeSessionRegistry, "is_live", staticmethod(lambda _record: next(live)))

    assert len(NativeSessionRegistry.scan([base])) == 1
    assert NativeSessionRegistry.scan([base]) == ()
    assert reads == 1


def test_is_live_rejects_missing_process(monkeypatch: pytest.MonkeyPatch) -> None:
    native = NativeClaudeSession(1, "s", "/w", None, "busy", "42")
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert not NativeSessionRegistry.is_live(native)


def test_is_live_compares_proc_start(monkeypatch: pytest.MonkeyPatch) -> None:
    native = NativeClaudeSession(12, "s", "/w", None, "idle", "42")
    # ``stat`` field 22 is starttime; after field 3, it is index 19.
    stat = "12 (claude) S " + " ".join(["0"] * 18 + ["42", "0"])
    monkeypatch.setattr(Path, "read_text", lambda _path, **_kwargs: stat)
    assert NativeSessionRegistry.is_live(native)

    wrong = NativeClaudeSession(12, "s", "/w", None, "idle", "41")
    assert not NativeSessionRegistry.is_live(wrong)


def test_is_live_rejects_a_zombie_process(monkeypatch: pytest.MonkeyPatch) -> None:
    native = NativeClaudeSession(12, "s", "/w", None, "idle", None)
    stat = "12 (claude) Z " + " ".join(["0"] * 20)
    monkeypatch.setattr(Path, "read_text", lambda _path, **_kwargs: stat)

    assert not NativeSessionRegistry.is_live(native)
