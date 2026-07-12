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

from pathlib import Path
from typing import Any

import pytest

from grove.core import tmux
from grove.core.config import AgentSpec, GroveConfig
from grove.core.errors import TmuxError


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Capture every subprocess.run argv emitted by grove.core.tmux.

    Also neutralizes `time.sleep` (the settle/verify delays in
    `send_text`/`send_keys`, #180) so these tests don't actually block —
    dedicated timing tests monkeypatch `tmux.time.sleep` themselves to
    assert on the delay.
    """
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
    monkeypatch.setattr(tmux.time, "sleep", lambda _seconds: None)
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

    # A third call follows: the post-Enter verify-and-retry snapshot (#180).
    # The fixture's fake pane ("line1\nline2\n") never echoes the sent text,
    # so no residual is detected and no second Enter fires.
    assert fake_run == [
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "-please continue, then press Enter"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],
        ["tmux", "capture-pane", "-p", "-e", "-S", "-500", "-t", "sess:agent"],
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


def test_send_text_settles_before_enter_and_before_verify(
    fake_run: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The settle delay (#180) fires twice: once before the submitting Enter
    (so it lands after the paste-accumulation window closes) and once more
    before the post-Enter verify snapshot — both using the configured
    `settle_ms`, converted to seconds."""
    sleeps: list[float] = []
    monkeypatch.setattr(tmux.time, "sleep", sleeps.append)

    tmux.send_text("sess:agent", "hi", settle_ms=250)

    assert sleeps == [0.25, 0.25]


def test_send_text_settle_ms_zero_skips_delay(
    fake_run: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(tmux.time, "sleep", sleeps.append)

    tmux.send_text("sess:agent", "hi", settle_ms=0)

    assert sleeps == []


def test_send_text_retries_enter_once_when_composer_holds_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the post-Enter snapshot shows the composer still holding the sent
    text's tail — the swallowed-Enter race — resend Enter exactly once."""
    calls: list[list[str]] = []

    def _run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)

        class _R:
            returncode = 0
            stdout = "some prompt\nplease continue" if argv[1] == "capture-pane" else ""
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(tmux.time, "sleep", lambda _seconds: None)

    tmux.send_text("sess:agent", "please continue")

    assert calls == [
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "please continue"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],
        ["tmux", "capture-pane", "-p", "-e", "-S", "-500", "-t", "sess:agent"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],  # the single retry
    ]


def test_send_text_never_retries_more_than_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exactly one retry — never a loop — even though the fake pane keeps
    reporting the same residual text after the retry fires."""
    calls: list[list[str]] = []

    def _run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)

        class _R:
            returncode = 0
            stdout = "still here" if argv[1] == "capture-pane" else ""
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(tmux.time, "sleep", lambda _seconds: None)

    tmux.send_text("sess:agent", "still here")

    # payload, Enter, one capture-pane check, one retry Enter — no second check.
    assert calls.count(["tmux", "capture-pane", "-p", "-e", "-S", "-500", "-t", "sess:agent"]) == 1
    assert calls.count(["tmux", "send-keys", "-t", "sess:agent", "Enter"]) == 2


# ─── send_keys (#109) ────────────────────────────────────────────────────────


def test_send_keys_dispatches_literal_runs_and_named_keys(
    fake_run: list[list[str]],
) -> None:
    """Pin the per-op argv: a ``SendKey`` is a bare key name, a ``str`` is a
    literal ``-l --`` run. The dispatch order is load-bearing — a StrEnum IS a
    str, so a literal-first check would type "Tab"/"Enter" instead of pressing
    them. This is the exact op list the Claude adapter builds for a batch.
    """
    tmux.send_keys(
        "sess:agent",
        ["2", "1", "3", tmux.SendKey.TAB, tmux.SendKey.ENTER],
    )

    # The sequence ends in Enter, so a verify-and-retry snapshot follows it
    # (#180); the fixture pane never echoes "3" (the last literal run sent),
    # so no residual is detected and no second Enter fires.
    assert fake_run == [
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "2"],
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "1"],
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "3"],
        ["tmux", "send-keys", "-t", "sess:agent", "Tab"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],
        ["tmux", "capture-pane", "-p", "-e", "-S", "-500", "-t", "sess:agent"],
    ]


def test_send_keys_types_free_text_literally(fake_run: list[list[str]]) -> None:
    """A free-text run is sent with ``-l --`` so its content is typed verbatim,
    never interpreted as key names (a value like "Enter" would submit)."""
    tmux.send_keys("sess:agent", ["3", "Enter please", tmux.SendKey.ENTER])

    assert fake_run == [
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "3"],
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "Enter please"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],
        ["tmux", "capture-pane", "-p", "-e", "-S", "-500", "-t", "sess:agent"],
    ]


def test_send_keys_settles_only_before_a_terminal_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-terminal Enter (mid-sequence) is neither delayed nor verified —
    only the sequence's LAST op, if it's Enter, gets the send_text treatment."""
    sleeps: list[float] = []
    monkeypatch.setattr(tmux.time, "sleep", sleeps.append)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")

    def _run(_argv: list[str], **_kwargs: Any) -> Any:
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)

    # No trailing Enter: nothing "submits", so no settle/verify at all.
    tmux.send_keys("sess:agent", ["1", tmux.SendKey.TAB], settle_ms=250)
    assert sleeps == []

    sleeps.clear()
    # Trailing Enter: settle before it, then the verify wait.
    tmux.send_keys("sess:agent", ["1", tmux.SendKey.ENTER], settle_ms=250)
    assert sleeps == [0.25, 0.25]


def test_send_keys_retries_terminal_enter_once_on_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def _run(argv: list[str], **_kwargs: Any) -> Any:
        calls.append(argv)

        class _R:
            returncode = 0
            stdout = "> 2" if argv[1] == "capture-pane" else ""
            stderr = ""

        return _R()

    monkeypatch.setattr(tmux.subprocess, "run", _run)
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    monkeypatch.setattr(tmux.time, "sleep", lambda _seconds: None)

    tmux.send_keys("sess:agent", ["2", tmux.SendKey.ENTER])

    assert calls == [
        ["tmux", "send-keys", "-t", "sess:agent", "-l", "--", "2"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],
        ["tmux", "capture-pane", "-p", "-e", "-S", "-500", "-t", "sess:agent"],
        ["tmux", "send-keys", "-t", "sess:agent", "Enter"],  # the single retry
    ]


def test_send_keys_raises_when_tmux_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux.shutil, "which", lambda _: None)

    with pytest.raises(TmuxError):
        tmux.send_keys("sess:agent", [tmux.SendKey.ENTER])


def test_send_keys_raises_on_nonzero_exit_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Steering fails LOUDLY and stops at the first failed op — no further keys
    land in whatever pane is there."""
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
        tmux.send_keys("ghost:agent", ["1", tmux.SendKey.ENTER])
    assert len(calls) == 1  # stopped after the failed first op


# ─── build_workspace_layout — the hermetic launch env (#82) ──────────────────


class _FakePane:
    """Records every send_keys so we can assert the unset/export/command order."""

    def __init__(self) -> None:
        self.keys: list[str] = []

    def send_keys(self, cmd: str, *, enter: bool = True, suppress_history: bool = False) -> None:
        del enter, suppress_history
        self.keys.append(cmd)


class _FakeWindow:
    def __init__(self, pane: _FakePane) -> None:
        self.active_pane = pane

    def rename_window(self, _name: str) -> None: ...
    def select_window(self) -> None: ...


class _FakeSession:
    def __init__(self, window: _FakeWindow) -> None:
        self.windows = [window]
        self._agent_window = window

    def new_window(self, *, window_name: str, start_directory: str, attach: bool) -> _FakeWindow:
        del window_name, start_directory, attach
        return self._agent_window


class _FakeServer:
    def __init__(self, session: _FakeSession) -> None:
        self._session = session

        class _Sessions:
            def filter(self, *, session_name: str) -> list[_FakeSession]:
                del session_name
                return [session]

        self.sessions = _Sessions()


@pytest.fixture
def fake_pane(monkeypatch: pytest.MonkeyPatch) -> _FakePane:
    """Drive the real ``build_workspace_layout`` against an in-memory libtmux."""
    pane = _FakePane()
    server = _FakeServer(_FakeSession(_FakeWindow(pane)))
    monkeypatch.setattr(tmux, "_server", lambda: server)
    return pane


def _layout(pane_fixture: _FakePane, agent: AgentSpec) -> list[str]:
    # The layout takes structured primitives, not an AgentSpec (it sits below the
    # LaunchBackend seam, #145); unpack the fixture's agent the way the manager's
    # TmuxLaunchBackend does, so these #82 hermetic-env assertions still pin the
    # real keystroke sequence.
    tmux.build_workspace_layout(
        "test-sess",
        cfg=GroveConfig(),
        worktree=Path("/tmp/wt"),
        command=agent.command,
        env=agent.env,
        env_unset=agent.env_unset,
    )
    return pane_fixture.keys


def test_layout_unsets_before_export_so_pane_is_hermetic(fake_pane: _FakePane) -> None:
    """The leaked var is ``unset`` and a configured var ``export``ed, both before
    the command — so the pane's profile is decided by the agent, not the daemon."""
    agent = AgentSpec(
        name="claude",
        command="claude",
        env={"FOO": "bar"},
        env_unset=("CLAUDE_CONFIG_DIR",),
    )
    keys = _layout(fake_pane, agent)
    assert "unset CLAUDE_CONFIG_DIR" in keys
    assert "export FOO='bar'" in keys
    # unset and export both precede the launched command.
    assert keys.index("unset CLAUDE_CONFIG_DIR") < keys.index("claude")
    assert keys.index("export FOO='bar'") < keys.index("claude")


def test_layout_export_wins_over_unset_for_same_key(fake_pane: _FakePane) -> None:
    """A key in both ``env_unset`` and ``env`` ends up exported: unset runs first,
    so a user who pins ``CLAUDE_CONFIG_DIR`` via ``env`` gets that dir, not the
    cleared default."""
    agent = AgentSpec(
        name="claude",
        command="claude",
        env={"CLAUDE_CONFIG_DIR": "/work"},
        env_unset=("CLAUDE_CONFIG_DIR",),
    )
    keys = _layout(fake_pane, agent)
    assert keys.index("unset CLAUDE_CONFIG_DIR") < keys.index("export CLAUDE_CONFIG_DIR='/work'")


def test_layout_no_env_unset_emits_no_unset(fake_pane: _FakePane) -> None:
    """A plain agent (no ``env_unset``) emits no ``unset`` — the mechanism is opt-in
    per agent, not a blanket scrub."""
    agent = AgentSpec(name="shell", command="$SHELL")
    keys = _layout(fake_pane, agent)
    assert not any(k.startswith("unset ") for k in keys)
