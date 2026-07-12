"""tmux session/window helpers and the init-script runner.

Public surface is a handful of plain functions: create_session, kill_session,
has_session, build_workspace_layout, run_init_script, attach_instruction.
The TUI never imports libtmux directly — it goes through here.

All side effects (libtmux calls, subprocess) live in this module. Higher
layers (manager.py) compose these into the lifecycle.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import libtmux
from libtmux.constants import OptionScope
from libtmux.server import Server
from loguru import logger

from grove.core import paths
from grove.core.config import GroveConfig, InitScriptConfig
from grove.core.errors import TmuxError


class SendKey(StrEnum):
    """The closed vocabulary of named keys :func:`send_keys` can emit.

    Values are tmux key names, passed to ``send-keys`` WITHOUT ``-l`` so tmux
    interprets them as keypresses — a literal ``-l`` "Enter" would type five
    characters, not submit. Deliberately tiny: Tab / Enter / Escape are all an
    interactive selector (Claude Code's ``AskUserQuestion`` dialog) needs to
    navigate, submit, and cancel. Widening it is a conscious act, not a typo.
    """

    TAB = "Tab"
    ENTER = "Enter"
    ESCAPE = "Escape"


# One step for :func:`send_keys`: a named key, or a literal text run (``str``)
# typed verbatim via ``-l --``. ``SendKey`` subclasses ``str`` (it is a StrEnum),
# so callers MUST test ``isinstance(op, SendKey)`` before treating an op as
# literal text — the dispatch in ``send_keys`` does exactly that, in that order.
SendOp = SendKey | str


@dataclass(frozen=True, slots=True)
class AttachInstruction:
    """Returned by the manager; the client decides how to attach.

    The core deliberately does not exec — that would couple it to the TUI's
    process model. Instead, the TUI inspects this and either calls
    `tmux switch-client` (when already inside tmux) or `app.suspend()` +
    `tmux attach` (when launched from a plain pty).
    """

    tmux_session: str
    inside_outer_tmux: bool


def _server() -> Server:
    if shutil.which("tmux") is None:
        raise TmuxError("tmux not found on PATH — on Windows, run Grove inside WSL2")
    return libtmux.Server()


def has_session(name: str) -> bool:
    """True iff a tmux session with this exact name exists right now."""
    try:
        sessions = _server().sessions.filter(session_name=name)
    except Exception as exc:  # libtmux raises various subprocess-derived errors
        logger.debug("has_session({}) raised: {}", name, exc)
        return False
    return bool(sessions)


def create_session(name: str, cwd: Path, *, history_limit: int = 50_000) -> None:
    """Create a detached tmux session rooted at `cwd`. Idempotent if it already exists."""
    server = _server()
    if has_session(name):
        raise TmuxError(f"tmux session already exists: {name}")
    try:
        session = server.new_session(
            session_name=name,
            start_directory=str(cwd),
            attach=False,
        )
    except Exception as exc:
        raise TmuxError(f"failed to create tmux session {name}: {exc}") from exc
    try:
        session.set_option("history-limit", str(history_limit))
        session.set_option("mouse", "on")
        # Auto-resize windows to whichever client is currently attached.
        # Without this, a workspace pane stays pinned at whatever size the
        # most-recent tmux client used; re-attaching from a larger terminal
        # leaves a dotted-shaded gap around the content (tmux's "window
        # smaller than client" indicator). `latest` is tmux's modern default
        # but isn't guaranteed everywhere — set explicitly per session so
        # Grove's behavior doesn't depend on the user's global tmux config.
        session.set_option("window-size", "latest", scope=OptionScope.Window)
    except Exception as exc:
        logger.warning("could not set tmux options on {}: {}", name, exc)


def kill_session(name: str) -> None:
    """Kill the session if it exists; no-op otherwise."""
    if not has_session(name):
        return
    try:
        _server().kill_session(target_session=name)
    except Exception as exc:
        raise TmuxError(f"failed to kill tmux session {name}: {exc}") from exc


def build_workspace_layout(
    session_name: str,
    *,
    cfg: GroveConfig,
    worktree: Path,
    command: str,
    decoration: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    env_unset: Sequence[str] = (),
) -> None:
    """Set up windows inside an existing session: shell + agent.

    Window 0 is renamed to the configured shell name; a new window is added
    for the agent, `command` is sent into it, and that window is selected so
    it's frontmost on attach.

    Takes the launch as structured primitives — not an ``AgentSpec`` — because
    it sits below the ``LaunchBackend`` seam (#145): the manager composes an
    ``AgentSpec`` into a ``LaunchSpec`` and the tmux backend unpacks it here, so
    this side-effect surface stays decoupled from the config model.

    `decoration` is extra argv the manager threads in from the agent's adapter —
    Claude Code's `["--session-id", uuid]`, which is what makes transcript
    correlation deterministic. It is shell-quoted and appended to `command`. The
    decoration is never hard-coded here: tmux.py is the side-effect surface, the
    adapter owns *what* the tokens are.

    The pane's env is made hermetic before the command runs (issue #82): a pane
    inherits the tmux server env (which inherited the daemon's), so `env_unset`
    is `unset` first to drop any leaked ambient value, then `env` is exported.
    Unset-before-export means a key in both ends up exported — `env` wins.
    """
    server = _server()
    sessions = server.sessions.filter(session_name=session_name)
    if not sessions:
        raise TmuxError(f"tmux session not found: {session_name}")
    session = sessions[0]

    # Rename the default first window → shell
    first = session.windows[0]
    try:
        first.rename_window(cfg.tmux.shell_window_name)
    except Exception as exc:
        logger.warning("rename window failed: {}", exc)

    # Add agent window
    try:
        agent_window = session.new_window(
            window_name=cfg.tmux.agent_window_name,
            start_directory=str(worktree),
            attach=False,
        )
    except Exception as exc:
        raise TmuxError(f"failed to create agent window: {exc}") from exc

    pane = agent_window.active_pane
    if pane is None:
        raise TmuxError("agent window has no pane")

    # Make the pane's profile hermetic BEFORE the command inherits the ambient
    # value: unset the leaked vars first, then export agent-specific env — so we
    # need no agent stdout sniffing or external env-injection. Unset-before-export
    # means a key in both `env_unset` and `env` ends up exported (#82).
    for key in env_unset:
        pane.send_keys(f"unset {key}", enter=True, suppress_history=True)
    for key, value in (env or {}).items():
        pane.send_keys(f"export {key}={_shell_quote(value)}", enter=True, suppress_history=True)

    if decoration:
        command = " ".join([command, *(shlex.quote(token) for token in decoration)])
    pane.send_keys(command, enter=True)

    try:
        agent_window.select_window()
    except Exception as exc:
        logger.debug("could not select agent window: {}", exc)


def run_init_script(
    cfg: InitScriptConfig,
    *,
    worktree: Path,
    repo_root: Path,
    extra_env: dict[str, str] | None = None,
    log_path: Path | None = None,
) -> int:
    """Run the configured init script in `worktree`. Returns the exit code.

    `log_path`, if provided, receives stdout + stderr so a failed init is
    diagnosable from the peek rail without the user reopening their shell.
    Loguru still gets the same content for `grove debug`.
    """
    if not cfg.enabled:
        return 0
    if cfg.inline and cfg.path:
        raise TmuxError("init_script: specify either `inline` or `path`, not both")

    if cfg.inline:
        cmd = [cfg.shell, "-c", cfg.inline]
    elif cfg.path:
        script_path = (repo_root / cfg.path).resolve()
        if not script_path.is_file():
            raise TmuxError(f"init_script path not found: {script_path}")
        cmd = [cfg.shell, str(script_path)]
    else:
        return 0

    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    logger.info("running init script: {} (timeout={}s)", " ".join(cmd), cfg.timeout_seconds)
    try:
        result = subprocess.run(
            cmd,
            cwd=str(worktree),
            env=env,
            capture_output=True,
            text=True,
            timeout=cfg.timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise TmuxError(f"init script exceeded timeout of {cfg.timeout_seconds}s") from None
    if result.stdout.strip():
        logger.info("init stdout:\n{}", result.stdout.strip())
    if result.stderr.strip():
        logger.warning("init stderr:\n{}", result.stderr.strip())
    if log_path is not None:
        try:
            paths.ensure_dir(log_path.parent)
            log_path.write_text(
                f"$ {' '.join(cmd)}\n--- stdout ---\n{result.stdout}"
                f"--- stderr ---\n{result.stderr}",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("could not write init log to {}: {}", log_path, exc)
    return result.returncode


def capture_pane_snapshot(target: str, *, history_lines: int = 500) -> str:
    """Read-only snapshot of a tmux pane, INCLUDING scrollback.

    `target` is a tmux target spec (`session`, `session:window`, or
    `session:window.pane`). Best-effort: returns "" on any failure
    (missing tmux, dead session, bad target). Never raises — `peek()`
    must keep rendering even if tmux has gone away.

    Returns the captured grid as-is (the client owns the viewport: the
    rail/tile tail it, the webapp `<pre>` scrolls it), with only the
    viewport's trailing blank rows below the cursor trimmed. Core does
    NOT pre-crop to a fixed line count for everyone.

    Flags:
        -p  print to stdout
        -e  preserve SGR (color/attribute) escapes — capture-pane reads
            the rendered grid, not the input stream, so cursor-move CSI
            is never emitted; safe to feed straight to Text.from_ansi.
        -S -N  start N lines back into scrollback (the fix for "only the
            bottom of the session is ever shown" — without it tmux starts
            at the top of the *visible viewport* and history is never read).

    `-J` (rejoin wrapped lines) is deliberately NOT passed: it produces
    logical lines far wider than the pane, which the TUI rail (`no_wrap`)
    and the webapp `<pre>` then clip. The raw grid — one line per display
    row, already pane-width-bounded — is the faithful snapshot every
    consumer wants.
    """
    if shutil.which("tmux") is None:
        return ""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-e", "-S", f"-{history_lines}", "-t", target],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("capture_pane_snapshot({}) failed: {}", target, exc)
        return ""
    if result.returncode != 0 or not result.stdout:
        return ""
    out_lines = result.stdout.splitlines()
    # Trim only the viewport's trailing blank rows (the empty grid below the
    # cursor); keep interior/leading blanks — they're real screen content.
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return "\n".join(out_lines)


# Fallback settle delay when a caller doesn't pass `cfg.tmux.steer_settle_ms`
# explicitly (same pattern as `create_session`'s `history_limit` default) —
# mirrors `TmuxConfig.steer_settle_ms`'s own default (#180).
DEFAULT_STEER_SETTLE_MS = 200

# How much of the sent text's tail the residual-composer check compares
# against (see `_pane_holds_residual_text`). Bounded because tmux's rendered
# grid can wrap a long paste across several display rows — matching the very
# end is the reliable anchor, matching the whole payload is not.
_RESIDUAL_TAIL_CHARS = 40


def send_text(target: str, text: str, *, settle_ms: int = DEFAULT_STEER_SETTLE_MS) -> None:
    """Type `text` into the pane at `target`, then press Enter to submit it.

    Two deliberate ``send-keys`` calls, never one:

    * The payload call passes ``-l`` so tmux treats the text as a literal
      byte string — without it tmux interprets key *names*, so a message
      containing "Enter", "C-c", or "Escape" would be executed as
      keystrokes instead of typed as text. ``--`` ends option parsing so
      a payload starting with ``-`` can't be misread as a flag.
    * The submitting Enter is its own, non-``-l`` call: under ``-l`` the
      word "Enter" would just be five typed characters.

    A `settle_ms` delay sits between the two calls (#180): the TUI's
    bracketed paste buffers everything landing inside its accumulation
    window as literal text, so an Enter sent immediately after a paste can
    be coalesced into that same window and read as a literal newline rather
    than parsed as a lone submitting keypress — only a lone `return`
    keypress submits. Waiting past the window before sending Enter is what
    lets it land as a real keypress instead. After the Enter, one
    verify-and-retry pass (`_retry_enter_if_residual`) re-sends Enter
    exactly once if the composer still visibly holds the sent text's tail —
    the observable sign the first Enter was swallowed.

    Mechanism only — resolving *which* pane to steer is the manager's
    ``pane_target`` policy. Unlike this module's best-effort read helpers,
    failures raise ``TmuxError``: a steer that silently vanished is worse
    than one that failed loudly. The residual-retry race itself never
    raises on its own account — only a genuine subprocess failure on the
    retry send does, via that same loud contract.
    """
    if shutil.which("tmux") is None:
        raise TmuxError("tmux not found on PATH — on Windows, run Grove inside WSL2")
    _run_send_keys(target, ["-l", "--", text])
    _settle(settle_ms)
    _run_send_keys(target, ["Enter"])
    _retry_enter_if_residual(target, text, settle_ms=settle_ms)


def send_keys(
    target: str, ops: Sequence[SendOp], *, settle_ms: int = DEFAULT_STEER_SETTLE_MS
) -> None:
    """Drive an interactive TUI at `target` with an ordered op sequence.

    The narrow sibling of :func:`send_text` for tools whose UI is a keystroke
    protocol rather than a text box (Claude Code's ``AskUserQuestion`` selector).
    Each op is dispatched as its own ``send-keys`` call, in order:

    * a :class:`SendKey` (Tab/Enter/Escape) is sent by NAME — no ``-l`` — so tmux
      injects the keypress;
    * anything else is a literal text run, sent with ``-l --`` so tmux types it
      verbatim (a digit that selects an option, the characters of a free-text
      answer). ``--`` ends option parsing so a run starting with ``-`` can't be
      read as a flag.

    A terminal :attr:`SendKey.ENTER` — the last op in the sequence — gets the
    same two hardening measures `send_text` gives its Enter (#180): a
    `settle_ms` delay before it (so it lands after any bracketed-paste window
    a preceding literal run opened, never glued to it) and one
    verify-and-retry pass afterward against the most recent literal run sent
    (the tail a swallowed Enter would leave behind). A non-terminal Enter
    (nothing in today's grammar emits one, but the type permits it) is
    neither delayed nor verified — it hasn't submitted anything yet.

    Mechanism only — this module knows nothing of questions or grammars; the
    Claude adapter builds the op list, the manager resolves which pane. Like
    :func:`send_text`, failures raise ``TmuxError`` (a steer that silently
    vanished is worse than one that failed loudly). Best-effort ordering is NOT
    attempted otherwise: ops are sent as fast as subprocess spawns allow, so a
    TUI still painting can drop a keystroke — that residual race is the
    caller's to own.
    """
    if shutil.which("tmux") is None:
        raise TmuxError("tmux not found on PATH — on Windows, run Grove inside WSL2")
    last_literal = ""
    final_index = len(ops) - 1
    for index, op in enumerate(ops):
        is_terminal_enter = index == final_index and op is SendKey.ENTER
        if is_terminal_enter and index > 0:
            _settle(settle_ms)
        if isinstance(op, SendKey):
            _run_send_keys(target, [op.value])
        else:
            _run_send_keys(target, ["-l", "--", op])
            last_literal = op
        if is_terminal_enter:
            _retry_enter_if_residual(target, last_literal, settle_ms=settle_ms)


def _run_send_keys(target: str, key_args: list[str]) -> None:
    """Run one `tmux send-keys -t <target> <key_args...>`; raises `TmuxError`.

    The single subprocess invocation both `send_text` and `send_keys` dispatch
    per op, so the argv shape and error handling can't drift between them.
    """
    argv = ["tmux", "send-keys", "-t", target, *key_args]
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise TmuxError(f"send-keys to {target} failed: {exc}") from exc
    if result.returncode != 0:
        raise TmuxError(
            f"send-keys to {target} exited {result.returncode}: {result.stderr.strip()}"
        )


def _settle(settle_ms: int) -> None:
    """Sleep `settle_ms` milliseconds; a non-positive value skips the wait.

    Isolated to one line so tests can monkeypatch `tmux.time.sleep` instead of
    actually blocking on every steering test.
    """
    if settle_ms > 0:
        time.sleep(settle_ms / 1000)


def _retry_enter_if_residual(target: str, text: str, *, settle_ms: int) -> None:
    """Re-send the submitting Enter ONCE if the composer still holds `text`.

    After a bounded wait, snapshots the pane and checks whether its last
    non-blank line still ends with the sent text's tail — the visible sign
    the first Enter landed inside the bracketed-paste accumulation window and
    was absorbed as a literal newline instead of submitting (#180). Exactly
    one retry, never a loop: a genuinely stuck pane is a different problem,
    not something to spin on. Never raises on the race itself — `text` empty
    or `capture_pane_snapshot` failing (it's best-effort, returns "") both
    just skip the retry; only a real subprocess failure on the retry send
    raises, via `_run_send_keys`'s usual loud `TmuxError`.
    """
    if not text:
        return
    _settle(settle_ms)
    snapshot = capture_pane_snapshot(target)
    if _pane_holds_residual_text(snapshot, text):
        _run_send_keys(target, ["Enter"])


def _pane_holds_residual_text(snapshot: str, text: str) -> bool:
    """True if the pane's last non-blank line still ends with `text`'s tail.

    Compares a bounded tail (`_RESIDUAL_TAIL_CHARS`), not the whole payload:
    tmux's rendered grid can wrap a long paste across several display rows,
    so matching the very end is the reliable anchor — a submitted composer
    resets to an empty prompt line, a swallowed one still shows the pasted
    text trailing off the last row.
    """
    if not snapshot:
        return False
    tail = text.strip()[-_RESIDUAL_TAIL_CHARS:]
    if not tail:
        return False
    lines = [line for line in snapshot.splitlines() if line.strip()]
    if not lines:
        return False
    return tail in lines[-1]


def pane_activity_seconds_ago(target: str) -> int | None:
    """Seconds since `target`'s window last produced output, per tmux.

    `target` is a tmux target spec (`session:window` or `session:window.pane`).
    Reads the ``window_activity`` format variable — the epoch second of the
    last time any pane in the window emitted output — and compares to the
    local clock. We use ``window_activity`` rather than ``pane_activity``
    because the latter was added in newer tmux releases and returns an
    empty string on tmux ≤3.3 (verified on 3.2a, the version Ubuntu ships).
    For Grove's one-pane-per-window layout the two values are equivalent;
    ``window_activity`` is just available everywhere.

    Returns ``None`` on any failure (missing tmux, bad target, empty/non-
    numeric output, future timestamp). Best-effort: callers in the peek
    hot path must keep rendering even if tmux has gone away.
    """
    if shutil.which("tmux") is None:
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", target, "-F", "#{window_activity}"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("pane_activity_seconds_ago({}) failed: {}", target, exc)
        return None
    if result.returncode != 0:
        return None
    raw = result.stdout.strip()
    if not raw or not raw.isdigit():
        return None
    activity_epoch = int(raw)
    delta = int(time.time()) - activity_epoch
    # A future timestamp means clock skew or a tmux quirk; treat as unknown
    # rather than report a negative age.
    return delta if delta >= 0 else None


def list_windows(session: str) -> list[str]:
    """Names of windows in `session`, in tmux index order. Empty on failure.

    Mechanism — not policy. Picking *which* window to capture from when
    a workspace's session has been reorganized externally is the
    manager's job (`WorkspaceManager.pane_target`); this helper just
    reports what tmux says is there.

    Best-effort like the rest of this module: missing tmux, missing
    session, or any subprocess error returns ``[]`` rather than raising,
    so callers in the peek hot path keep rendering.
    """
    if shutil.which("tmux") is None:
        return []
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("list_windows({}) failed: {}", session, exc)
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def attach_instruction(session_name: str) -> AttachInstruction:
    """Build the structured instruction the client uses to attach.

    Detects whether we're already inside an outer tmux client by checking
    the standard `$TMUX` env var.
    """
    return AttachInstruction(
        tmux_session=session_name,
        inside_outer_tmux=bool(os.environ.get("TMUX")),
    )


# ─── internal ───────────────────────────────────────────────────────────────


def _shell_quote(value: str) -> str:
    """Quote a value for `export KEY=...`. Conservative single-quote wrap."""
    return "'" + value.replace("'", "'\\''") + "'"
