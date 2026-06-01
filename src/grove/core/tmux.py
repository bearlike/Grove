"""tmux session/window helpers and the init-script runner.

Public surface is a handful of plain functions: create_session, kill_session,
has_session, build_workspace_layout, run_init_script, attach_instruction.
The TUI never imports libtmux directly — it goes through here.

All side effects (libtmux calls, subprocess) live in this module. Higher
layers (manager.py) compose these into the lifecycle.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import libtmux
from libtmux.constants import OptionScope
from libtmux.server import Server
from loguru import logger

from grove.core.config import AgentSpec, GroveConfig, InitScriptConfig
from grove.core.errors import TmuxError


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
    agent: AgentSpec,
) -> None:
    """Set up windows inside an existing session: shell + agent.

    Window 0 is renamed to the configured shell name; a new window is added
    for the agent, the agent's command is sent into it, and that window is
    selected so it's frontmost on attach.
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

    # Export agent-specific env in the pane before launching the command,
    # so we don't need agent stdout sniffing or external env-injection.
    for key, value in agent.env.items():
        pane.send_keys(f"export {key}={_shell_quote(value)}", enter=True, suppress_history=True)

    pane.send_keys(agent.command, enter=True)

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
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"$ {' '.join(cmd)}\n--- stdout ---\n{result.stdout}"
                f"--- stderr ---\n{result.stderr}",
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("could not write init log to {}: {}", log_path, exc)
    return result.returncode


def capture_pane_snapshot(target: str, *, lines: int = 60) -> str:
    """Read-only snapshot of a tmux pane's visible content.

    `target` is a tmux target spec (`session`, `session:window`, or
    `session:window.pane`). Best-effort: returns "" on any failure
    (missing tmux, dead session, bad target). Never raises — `peek()`
    must keep rendering even if tmux has gone away.

    Flags:
        -p  print to stdout
        -e  preserve SGR (color/attribute) escapes — capture-pane reads
            the rendered grid, not the input stream, so cursor-move CSI
            is never emitted; safe to feed straight to Text.from_ansi.
        -J  rejoin lines wrapped by the source pane.
    """
    if shutil.which("tmux") is None:
        return ""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-e", "-J", "-t", target],
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
    return "\n".join(out_lines[-lines:])


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
