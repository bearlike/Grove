"""tmux.py — direct unit tests for the read-only helpers.

These pin the *flags* we pass to `tmux capture-pane` / `tmux resize-window`.
The flags are load-bearing: dropping `-e` strips colors, dropping `-S -N`
drops scrollback (only the bottom of the session ever shows), getting
`resize-window` wrong silently makes the source pane mismatch our
viewport. The fakes used elsewhere (FakeTmux) skip
this surface intentionally — they're the manager-level seam, not a
substitute for verifying the actual subprocess argv we emit.
"""

from __future__ import annotations

from typing import Any

import pytest

from grove.core import tmux
from grove.core.errors import TmuxError


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture every subprocess.run argv emitted by grove.core.tmux."""
    calls: list[list[str]] = []

    def _run(argv: list[str], **kwargs: Any) -> Any:
        del kwargs
        calls.append(argv)

        class _R:
            returncode = 0
            stdout = "line1\nline2\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    return calls


def test_capture_pane_snapshot_flags(fake_run: list[list[str]]) -> None:
    tmux.capture_pane_snapshot("sess:agent")

    assert len(fake_run) == 1
    argv = fake_run[0]
    assert argv[:2] == ["tmux", "capture-pane"]
    assert "-e" in argv  # SGR escapes preserved (color)
    assert "-p" in argv  # print to stdout
    assert "-t" in argv and "sess:agent" in argv
    # -S -<N> reads N lines of scrollback (the fix for "only the bottom shows").
    assert "-S" in argv
    assert argv[argv.index("-S") + 1] == "-500"
    # -J would rejoin wrapped lines into over-long ones the renderers clip.
    assert "-J" not in argv


def test_capture_pane_snapshot_history_window_is_configurable(
    fake_run: list[list[str]],
) -> None:
    tmux.capture_pane_snapshot("sess:agent", history_lines=1000)

    argv = fake_run[0]
    assert argv[argv.index("-S") + 1] == "-1000"


def test_capture_pane_snapshot_returns_full_capture_with_scrollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Model a `-S` capture: 200 content lines (scrollback above the viewport
    # included) plus the empty grid rows below the cursor.
    captured = "\n".join(f"L{n}" for n in range(200)) + "\n\n\n\n"

    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 0
            stdout = captured
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    out = tmux.capture_pane_snapshot("sess:agent")

    lines = out.splitlines()
    # No 60-line crop: every content line survives, in order.
    assert len(lines) == 200
    assert lines[0] == "L0"
    assert lines[-1] == "L199"  # trailing blank rows trimmed, not L199


def test_capture_pane_snapshot_returns_empty_when_tmux_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _: None)

    assert tmux.capture_pane_snapshot("sess:agent") == ""


def test_capture_pane_snapshot_swallows_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("tmux exploded")

    monkeypatch.setattr(tmux.subprocess, "run", _boom)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    # Best-effort contract: peek must keep rendering even if tmux is dead.
    assert tmux.capture_pane_snapshot("sess:agent") == ""


# ─── list_windows ────────────────────────────────────────────────────────────


def test_list_windows_emits_list_windows_with_F_window_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the argv: list-windows -t <session> -F #{window_name}.

    The format string is load-bearing: defaulting to tmux's verbose
    `idx: name (...)` would force us to parse, and we'd silently misread
    names that contain spaces or parens.
    """
    calls: list[list[str]] = []

    def _run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)

        class _R:
            returncode = 0
            stdout = "shell\nagent\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    out = tmux.list_windows("mysession")

    assert out == ["shell", "agent"]
    assert len(calls) == 1
    argv = calls[0]
    assert argv[:2] == ["tmux", "list-windows"]
    assert "-t" in argv and "mysession" in argv
    assert "-F" in argv and "#{window_name}" in argv


def test_list_windows_returns_empty_when_tmux_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _: None)

    assert tmux.list_windows("anything") == []


def test_list_windows_returns_empty_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 1  # session not found
            stdout = ""
            stderr = "no such session"

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    # Best-effort: a missing session must not bubble up as an exception.
    assert tmux.list_windows("ghost") == []


def test_list_windows_swallows_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("tmux exploded")

    monkeypatch.setattr(tmux.subprocess, "run", _boom)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    # Best-effort: peek must keep rendering even if tmux dies.
    assert tmux.list_windows("sess") == []


# ─── pane_activity_seconds_ago ───────────────────────────────────────────────


def test_pane_activity_uses_display_message_with_window_activity_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the argv: tmux display-message -p -t <target> -F #{window_activity}.

    This is what feeds the Active/Idle reconciliation. We deliberately use
    ``window_activity`` rather than ``pane_activity`` because tmux ≤3.3
    (Ubuntu 22.04 ships 3.2a) returns an empty string for the latter;
    every workspace would silently fall through to IDLE on those systems.
    For Grove's one-pane-per-window layout the values are equivalent. If
    the format string drifts (e.g. back to pane_activity, or drops the
    braces), tmux silently emits a literal and we'd parse the wrong thing.
    """
    calls: list[list[str]] = []
    now = 1_700_000_000

    def _run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)

        class _R:
            returncode = 0
            stdout = f"{now - 3}\n"  # 3 seconds ago
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(tmux.time, "time", lambda: float(now))

    age = tmux.pane_activity_seconds_ago("sess:agent")

    assert age == 3
    argv = calls[0]
    assert argv[:2] == ["tmux", "display-message"]
    assert "-p" in argv
    assert "-t" in argv and "sess:agent" in argv
    assert "-F" in argv and "#{window_activity}" in argv
    # Regression guard: the older tmux-3.4-only `pane_activity` must not
    # leak back in — it returns empty on Ubuntu's stock tmux 3.2a.
    assert "#{pane_activity}" not in argv


def test_pane_activity_returns_none_when_tmux_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _: None)
    assert tmux.pane_activity_seconds_ago("sess:agent") is None


def test_pane_activity_returns_none_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 1
            stdout = ""
            stderr = "no such window"

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    assert tmux.pane_activity_seconds_ago("ghost:agent") is None


def test_pane_activity_returns_none_for_non_numeric_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older tmux returns 'unknown' for pane_activity if the format isn't
    supported; reject anything that isn't a clean integer."""

    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 0
            stdout = "n/a\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    assert tmux.pane_activity_seconds_ago("sess:agent") is None


def test_pane_activity_treats_future_timestamps_as_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clock skew or a malformed pane_activity could yield a future epoch.
    Returning a negative age would mislead the reconciler; treat as unknown."""
    now = 1_700_000_000

    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 0
            stdout = f"{now + 60}\n"  # 60s in the future
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(tmux.time, "time", lambda: float(now))
    assert tmux.pane_activity_seconds_ago("sess:agent") is None


def test_pane_activity_swallows_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("tmux exploded")

    monkeypatch.setattr(tmux.subprocess, "run", _boom)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    # Best-effort like the rest of the module — peek must keep rendering
    # even when activity can't be measured.
    assert tmux.pane_activity_seconds_ago("sess:agent") is None


def test_list_windows_skips_blank_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defensive: tmux normally emits one name per line, but trailing
    newlines or blank lines (from edge cases on some platforms) must
    not become phantom ``""`` window names that the policy then tries
    to address as ``session:``.
    """

    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 0
            stdout = "shell\n\nagent\n\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    assert tmux.list_windows("sess") == ["shell", "agent"]


# ─── send_text ───────────────────────────────────────────────────────────────


def test_send_text_emits_literal_payload_then_separate_enter(
    fake_run: list[list[str]],
) -> None:
    """Pin the exact argv pair: `send-keys -l -- <text>`, then `send-keys Enter`.

    Every token is load-bearing: dropping `-l` makes tmux interpret key
    names (a message containing "Enter" or "C-c" becomes keystrokes),
    dropping `--` makes a leading-dash payload parse as a flag, and
    folding Enter into the literal call would type the word instead of
    submitting. Verified against real tmux 3.2a on 2026-06-11.
    """
    tmux.send_text("sess:agent", "-please continue, then press Enter")

    assert fake_run == [
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "-please continue, then press Enter"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],
    ]


def test_send_text_raises_when_tmux_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _: None)

    with pytest.raises(TmuxError):
        tmux.send_text("sess:agent", "hello")


def test_send_text_raises_on_nonzero_exit_and_skips_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike the read helpers, steering fails LOUDLY — and a failed payload
    must not be followed by a stray Enter into whatever pane is there."""
    calls: list[list[str]] = []

    def _run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)

        class _R:
            returncode = 1
            stdout = ""
            stderr = "no such pane"

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    with pytest.raises(TmuxError, match="no such pane"):
        tmux.send_text("ghost:agent", "hello")
    assert len(calls) == 1  # the Enter call never fired


def test_send_text_wraps_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise OSError("tmux exploded")

    monkeypatch.setattr(tmux.subprocess, "run", _boom)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    with pytest.raises(TmuxError):
        tmux.send_text("sess:agent", "hello")
