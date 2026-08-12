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
from typing import ClassVar, Final

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
class HostAttach:
    """Attach to a session on THIS host's tmux server.

    Every host workspace, and the one container case that has no in-container
    tmux to own the agent (``container.tmux_command == ""``), where the agent
    really does run in a host pane.
    """

    tmux_session: str
    inside_outer_tmux: bool

    def terminal_argv(self) -> list[str]:
        """The command that hands a terminal the caller OWNS to this session.

        ``switch-client`` when the caller is already inside an outer tmux
        client — re-point that client rather than nesting one inside itself —
        else a plain ``attach``. A caller with a FRESH pty (the daemon's
        xterm.js bridge) is never inside an outer client and must not read this
        flag; it composes its own argv off the wire mirror.
        """
        action = ["switch-client", "-t"] if self.inside_outer_tmux else ["attach", "-t"]
        return ["tmux", *action, self.tmux_session]


@dataclass(frozen=True, slots=True)
class ContainerAttach:
    """Enter the container and attach to the tmux INSIDE it.

    There is no host multiplexer in this arm and deliberately so: a host tmux
    session whose pane merely runs ``devcontainer exec … tmux attach`` is a
    shadow of the session it displays, and — measured — its pane stays on as a
    client that CLAMPS every later client's size to its own.

    ``argv`` is already complete (``devcontainer exec … -- tmux new-session -A
    -s <session>``), composed by the engine from the same
    :class:`~grove.core.container_agent.ContainerAgentEntry` that started the
    agent, so the way in and the way it was launched cannot drift.
    """

    argv: tuple[str, ...]

    def terminal_argv(self) -> list[str]:
        """The command that hands a terminal the caller owns to the container.

        Identical for every caller: entering another tmux SERVER is an exec, not
        a ``switch-client`` (which can only re-point a client within its own
        server), so ``inside_outer_tmux`` has no meaning here and this variant
        does not carry it.
        """
        return list(self.argv)


#: Where a client should attach, and to WHOSE multiplexer — the one question
#: whose answer differs by runtime. Kept a union rather than a nullable field on
#: one shape so neither arm can be read with the other's fields.
type AttachInstruction = HostAttach | ContainerAttach


#: The argv every tmux command in this module starts with — this host's own
#: ``tmux`` on ``PATH``.
#:
#: It is a *variable* rather than a literal because a tmux server is not always
#: reachable as a local binary: a containerized workspace runs its agent under a
#: tmux INSIDE the container, which is reached by prefixing
#: ``docker exec … <in-container tmux>``. That is the whole generalization — a
#: tmux command differs only in the argv that gets you to the server, so every
#: read/steer helper here takes a ``command`` and stays otherwise identical.
#: Nothing in this module knows what a container is; the caller supplies the
#: prefix (see :class:`grove.core.container_tmux.ContainerTmux`).
DEFAULT_TMUX_COMMAND: Final[tuple[str, ...]] = ("tmux",)


@dataclass(frozen=True, slots=True)
class PaneReport:
    """What tmux itself says about one pane: is its process dead, and when last active.

    The read behind two questions Grove used to answer with a shell recorder and
    a host heartbeat: *did the agent exit* (and with what status), and *when did
    it last produce output*. Both come from tmux's own format variables in ONE
    invocation, which matters because for a container workspace an invocation is
    a ``docker exec`` (measured at 60 ms — see
    :class:`grove.core.container_tmux.ContainerTmux`) and three questions must
    not cost three of them.

    ``pane_dead``/``pane_dead_status`` are only ever populated when the window
    carries ``remain-on-exit`` — without it tmux destroys the pane, the window
    and (for a one-window session) the session itself the instant the process
    exits, so there is nothing left to ask.
    """

    dead: bool
    exit_status: int | None
    activity_epoch: int | None

    #: One ``list-panes -F`` line per pane. ``|`` rather than a tab because the
    #: fields are three small scalars and a tab in this position buys nothing
    #: but an invisible separator to get wrong.
    FORMAT: ClassVar[str] = "#{pane_dead}|#{pane_dead_status}|#{window_activity}"

    @classmethod
    def parse(cls, raw: str) -> PaneReport | None:
        """The first pane's report, or ``None`` when tmux answered nothing.

        Pinned against payloads captured from a real tmux 3.5a inside a real
        container: ``0||1785471784`` for a live pane (an empty
        ``pane_dead_status`` — tmux emits the variable and leaves it blank),
        ``1|7|1785471399`` after the agent exited 7. ``None`` for empty output,
        which is what a missing session or a server that is not running yields
        alongside a non-zero exit.

        First line only: Grove's layout is one pane per window per session, and
        a second pane would be something a human added — its liveness is not
        the agent's.
        """
        line = next((entry for entry in raw.splitlines() if entry.strip()), "")
        if not line:
            return None
        parts = line.split("|")
        dead = parts[0].strip() == "1"
        status = parts[1].strip() if len(parts) > 1 else ""
        activity = parts[2].strip() if len(parts) > 2 else ""
        return cls(
            dead=dead,
            exit_status=int(status) if status.isdigit() else None,
            activity_epoch=int(activity) if activity.isdigit() else None,
        )

    def activity_seconds_ago(self) -> int | None:
        """Age of the last output, in seconds — the ``pane_activity_seconds_ago`` value.

        Same derivation and same honesty as that function (see its docstring for
        why ``window_activity`` and not ``pane_activity``): unknown or a future
        timestamp answers ``None`` rather than a negative age.
        """
        return _seconds_since(self.activity_epoch)


@dataclass(frozen=True, slots=True)
class SessionReport:
    """What tmux says about one SESSION on a server: name, clients, last activity.

    :class:`PaneReport`'s sibling one level up, and it exists for the same
    reason: a container's tmux server hosts SEVERAL sessions — the agent's, the
    interactive shell's, and any number of additional agents
    a user asked for — so "which agents are running in this container" is a
    question about the server's session list, not about one pane.

    Enumerating rather than persisting is the whole design decision behind that
    story: the container's own tmux server is the source of truth for which
    agents exist there, exactly as the host tmux server is already the source of
    truth for :func:`has_session` and :func:`list_windows`. Nothing about an
    extra agent is written to the workspace record, so nothing can go stale and
    nothing needs a migration.
    """

    name: str
    attached: bool
    activity_epoch: int | None

    #: One ``list-sessions -F`` line per session. Same ``|`` separator and same
    #: three-small-scalars shape as :attr:`PaneReport.FORMAT`.
    FORMAT: ClassVar[str] = "#{session_name}|#{session_attached}|#{session_activity}"

    @classmethod
    def parse_all(cls, raw: str) -> tuple[SessionReport, ...]:
        """Every session tmux listed, in tmux's own order.

        Pinned against a payload captured from a real tmux 3.4:
        ``0|1|1785476864`` — name, attached-client COUNT (not a boolean; tmux
        emits how many clients are attached), activity epoch. A session name
        may legitimately contain nothing tmux escapes for us, so the split is
        bounded to the two separators the format itself introduces and the name
        keeps whatever is left.

        Empty output yields ``()`` — what a server with no sessions, or no
        server at all, answers alongside a non-zero exit.
        """
        reports: list[SessionReport] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            name, _, rest = line.rpartition("|")
            name, _, attached = name.rpartition("|")
            if not name:
                continue
            reports.append(
                cls(
                    name=name,
                    attached=attached.strip().isdigit() and int(attached.strip()) > 0,
                    activity_epoch=int(rest.strip()) if rest.strip().isdigit() else None,
                )
            )
        return tuple(reports)

    def activity_seconds_ago(self) -> int | None:
        """Age of this session's last activity, in seconds; ``None`` when unknown."""
        return _seconds_since(self.activity_epoch)


def _seconds_since(epoch: int | None) -> int | None:
    """Seconds since a tmux epoch stamp — the one derivation both reports share.

    Unknown or a future timestamp answers ``None`` rather than a negative age:
    a clock that disagrees is not evidence of recent activity.
    """
    if epoch is None:
        return None
    delta = int(time.time()) - epoch
    return delta if delta >= 0 else None


@dataclass(frozen=True, slots=True)
class TmuxPane:
    """One tmux pane, bound to the argv that reaches the server hosting it.

    The value object the manager's pane policy returns, so a caller carries
    *where the pane is* and *how to talk to it* as one fact instead of two
    parallel arguments it could pair up wrongly. A host workspace's pane keeps
    the default command and behaves exactly as before this existed; a container
    workspace's agent pane lives inside its container and its command is a
    ``docker exec`` prefix.

    ``label`` is what clients and audit events display. It exists because the
    two panes are not interchangeable to a *reader*: ``grove-x:agent`` is a
    target a human can hand to their own ``tmux``, while the in-container
    session name is meaningless on this host — so the container form is rendered
    unmistakably rather than passed off as something attachable.
    """

    target: str
    command: tuple[str, ...] = DEFAULT_TMUX_COMMAND
    label: str = ""

    @property
    def display(self) -> str:
        """The human/wire-facing handle — ``label`` when one was supplied."""
        return self.label or self.target

    def capture(self, *, history_lines: int = 500) -> str:
        return capture_pane_snapshot(self.target, history_lines=history_lines, command=self.command)

    def send_text(self, text: str, *, settle_ms: int = 200) -> None:
        send_text(self.target, text, settle_ms=settle_ms, command=self.command)

    def send_keys(self, ops: Sequence[SendOp], *, settle_ms: int = 200) -> None:
        send_keys(self.target, ops, settle_ms=settle_ms, command=self.command)


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
    exit_suffix: str = "",
    shell_command: str = "",
) -> None:
    """Set up windows inside an existing session: shell + agent.

    Window 0 is renamed to the configured shell name; a new window atomically
    launches the agent command as its window shell, and that window is selected
    so it's frontmost on attach.

    `shell_command` (empty by default) replaces window 0's pane after the agent
    window exists. Empty leaves it exactly as it always was — an interactive
    host shell rooted at `worktree` — which is the right and only answer for a
    host workspace. A CONTAINER runtime passes the command that crosses into
    its namespace, because a host shell beside a container the user believes
    they are inside is worse than no shell window: the worktree it shows may
    not be the view the agent has. Mechanism only — this module never decides
    *what* that command is, exactly as it never composes the agent's decoration.

    Takes the launch as structured primitives — not an ``AgentSpec`` — because
    it sits below the ``LaunchBackend`` seam: the manager composes an
    ``AgentSpec`` into a ``LaunchSpec`` and the tmux backend unpacks it here, so
    this side-effect surface stays decoupled from the config model.

    `decoration` is extra argv the manager threads in from the agent's adapter —
    Claude Code's `["--session-id", uuid]`, which is what makes transcript
    correlation deterministic. It is shell-quoted and appended to `command`. The
    decoration is never hard-coded here: tmux.py is the side-effect surface, the
    adapter owns *what* the tokens are.

    The pane's env is made hermetic before the command runs: a pane
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

    if decoration:
        command = " ".join([command, *(shlex.quote(token) for token in decoration)])
    # The caller owns the verbatim exit recorder, and it must follow decoration.
    command += exit_suffix
    window_shell = _agent_window_shell(command, env=env, env_unset=env_unset)

    # Add agent window
    try:
        agent_window = session.new_window(
            window_name=cfg.tmux.agent_window_name,
            start_directory=str(worktree),
            attach=False,
            window_shell=window_shell,
        )
    except Exception as exc:
        raise TmuxError(f"failed to create agent window: {exc}") from exc

    pane = agent_window.active_pane
    if pane is None:
        raise TmuxError("agent window has no pane")

    if shell_command:
        shell_pane = first.active_pane
        if shell_pane is None:
            # Best-effort, unlike the agent window's own missing-pane raise: the
            # workspace is perfectly usable with a plain host shell here, so a
            # tmux that cannot hand us window 0's pane must not fail a create.
            logger.warning("shell window has no pane; leaving it a host shell")
        else:
            # Create the agent window first so a short-lived bridge command
            # cannot close window zero and take the whole session with it.
            shell_pane.cmd("respawn-pane", "-k", shell_command)

    try:
        agent_window.select()
    except Exception as exc:
        logger.debug("could not select agent window: {}", exc)


def _agent_window_shell(
    command: str,
    *,
    env: Mapping[str, str] | None,
    env_unset: Sequence[str],
) -> str:
    """Launch atomically through the user's login-interactive shell.

    Environment statements precede the agent, and a final interactive shell
    keeps the window attachable after it exits. This preserves ordinary shell
    startup while avoiding key injection into a prompt that is not ready yet.
    """
    shell = os.environ.get("SHELL") or "/bin/sh"
    statements = [*(f"unset {key}" for key in env_unset)]
    statements.extend(f"export {key}={_shell_quote(value)}" for key, value in (env or {}).items())
    statements.extend((command, f"exec {shlex.quote(shell)} -l"))
    script = "; ".join(statements)
    return f"{shlex.quote(shell)} -lic {shlex.quote(script)}"


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


def capture_pane_snapshot(
    target: str,
    *,
    history_lines: int = 500,
    command: Sequence[str] = DEFAULT_TMUX_COMMAND,
) -> str:
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

    `command` selects WHICH tmux server (see :data:`DEFAULT_TMUX_COMMAND`).
    Reading a container workspace's agent pane through its own in-container
    tmux rather than through the host pane that displays it is not merely
    equivalent: the host pane holds a tmux *client*, which repaints a viewport
    and keeps no scrollback of its own, so the same capture there returns ~one
    screen plus the in-container status bar. Measured against an agent that
    had printed 120 lines: host pane 22 of them, in-container pane all 120
    and no chrome.
    """
    if shutil.which(command[0]) is None:
        return ""
    try:
        result = subprocess.run(
            [*command, "capture-pane", "-p", "-e", "-S", f"-{history_lines}", "-t", target],
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
# mirrors `TmuxConfig.steer_settle_ms`'s own default.
DEFAULT_STEER_SETTLE_MS = 200

# How much of the sent text's tail the residual-composer check compares
# against (see `_pane_holds_residual_text`). Bounded because tmux's rendered
# grid can wrap a long paste across several display rows — matching the very
# end is the reliable anchor, matching the whole payload is not.
_RESIDUAL_TAIL_CHARS = 40


def send_text(
    target: str,
    text: str,
    *,
    settle_ms: int = DEFAULT_STEER_SETTLE_MS,
    command: Sequence[str] = DEFAULT_TMUX_COMMAND,
) -> None:
    """Type `text` into the pane at `target`, then press Enter to submit it.

    Two deliberate ``send-keys`` calls, never one:

    * The payload call passes ``-l`` so tmux treats the text as a literal
      byte string — without it tmux interprets key *names*, so a message
      containing "Enter", "C-c", or "Escape" would be executed as
      keystrokes instead of typed as text. ``--`` ends option parsing so
      a payload starting with ``-`` can't be misread as a flag.
    * The submitting Enter is its own, non-``-l`` call: under ``-l`` the
      word "Enter" would just be five typed characters.

    A `settle_ms` delay sits between the two calls: the TUI's
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

    `command` selects which tmux server (see :data:`DEFAULT_TMUX_COMMAND`): a
    container workspace's agent reads its keystrokes from the tmux inside its
    own container, which is what keeps steering working with no host pane in
    the picture at all.
    """
    if shutil.which(command[0]) is None:
        raise TmuxError(f"{command[0]} not found on PATH — on Windows, run Grove inside WSL2")
    _run_send_keys(target, ["-l", "--", text], command=command)
    _settle(settle_ms)
    _run_send_keys(target, ["Enter"], command=command)
    _retry_enter_if_residual(target, text, settle_ms=settle_ms, command=command)


def send_keys(
    target: str,
    ops: Sequence[SendOp],
    *,
    settle_ms: int = DEFAULT_STEER_SETTLE_MS,
    command: Sequence[str] = DEFAULT_TMUX_COMMAND,
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
    same two hardening measures `send_text` gives its Enter: a
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
    if shutil.which(command[0]) is None:
        raise TmuxError(f"{command[0]} not found on PATH — on Windows, run Grove inside WSL2")
    last_literal = ""
    final_index = len(ops) - 1
    for index, op in enumerate(ops):
        is_terminal_enter = index == final_index and op is SendKey.ENTER
        if is_terminal_enter and index > 0:
            _settle(settle_ms)
        if isinstance(op, SendKey):
            _run_send_keys(target, [op.value], command=command)
        else:
            _run_send_keys(target, ["-l", "--", op], command=command)
            last_literal = op
        if is_terminal_enter:
            _retry_enter_if_residual(target, last_literal, settle_ms=settle_ms, command=command)


def _run_send_keys(
    target: str, key_args: list[str], *, command: Sequence[str] = DEFAULT_TMUX_COMMAND
) -> None:
    """Run one `tmux send-keys -t <target> <key_args...>`; raises `TmuxError`.

    The single subprocess invocation both `send_text` and `send_keys` dispatch
    per op, so the argv shape and error handling can't drift between them.
    """
    argv = [*command, "send-keys", "-t", target, *key_args]
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


def _retry_enter_if_residual(
    target: str, text: str, *, settle_ms: int, command: Sequence[str] = DEFAULT_TMUX_COMMAND
) -> None:
    """Re-send the submitting Enter ONCE if the composer still holds `text`.

    After a bounded wait, snapshots the pane and checks whether its last
    non-blank line still ends with the sent text's tail — the visible sign
    the first Enter landed inside the bracketed-paste accumulation window and
    was absorbed as a literal newline instead of submitting. Exactly
    one retry, never a loop: a genuinely stuck pane is a different problem,
    not something to spin on. Never raises on the race itself — `text` empty
    or `capture_pane_snapshot` failing (it's best-effort, returns "") both
    just skip the retry; only a real subprocess failure on the retry send
    raises, via `_run_send_keys`'s usual loud `TmuxError`.
    """
    if not text:
        return
    _settle(settle_ms)
    snapshot = capture_pane_snapshot(target, command=command)
    if _pane_holds_residual_text(snapshot, text):
        _run_send_keys(target, ["Enter"], command=command)


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
    return _list_window_field(session, "#{window_name}")


def fit_window_to_client(session: str) -> None:
    """Hand sizing of every window in `session` back to tmux. Call before attach.

    This is what an attach does INSTEAD of ``tmux resize-window -x/-y``, which
    is wrong twice over:

    * It PINS the window to ``window-size: manual``. tmux then never re-fits it
      again — not on the next attach, not when the terminal itself is resized.
      The pin outlives the attach, and nothing clears it but an explicit set.
    * The dimensions then have to be computed, and ``#{client_height}`` counts
      rows the window never gets: the status bar's. Passing the client's full
      height therefore leaves the window N rows too tall for an N-row status
      bar, and tmux paints those bottom N rows — the agent's own footer and
      composer — underneath it, where the user cannot see them.

    ``window-size: latest`` hands both problems back to tmux: it sizes the
    window to whichever client most recently used it, subtracting the status
    rows itself. Setting it is also what CLEARS an inherited pin, which is why
    this runs on every attach and over every window — sessions Grove did not
    create (claude-squad imports) arrive with ``manual`` already set, and the
    pin is per-window, not per-session.

    Order matters: run this BEFORE ``switch-client``/``attach`` so the client's
    own resize pass applies the new policy immediately. Setting it afterwards
    also works, but tmux only re-fits a window once a client actually uses it,
    so the correction lands a beat late — visibly, on the user's first frame.

    Best-effort like this module's other read helpers: a missing tmux or a dead
    session leaves sizing alone rather than blocking the attach.
    """
    for window_id in _list_window_field(session, "#{window_id}"):
        try:
            subprocess.run(
                ["tmux", "set-option", "-w", "-t", window_id, "window-size", "latest"],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
                timeout=2,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("fit_window_to_client({}) failed: {}", window_id, exc)


def attach_instruction(session_name: str) -> HostAttach:
    """Build the structured instruction for attaching to a HOST session.

    Detects whether we're already inside an outer tmux client by checking
    the standard `$TMUX` env var. The container arm is composed by the manager
    (it needs the workspace's container identity, which this module has no
    business knowing) — see :class:`ContainerAttach`.
    """
    return HostAttach(
        tmux_session=session_name,
        inside_outer_tmux=bool(os.environ.get("TMUX")),
    )


# ─── internal ───────────────────────────────────────────────────────────────


def _list_window_field(session: str, field: str) -> list[str]:
    """One `field` value per window in `session`, in tmux index order.

    The shared body behind :func:`list_windows` (which wants ``#{window_name}``)
    and :func:`fit_window_to_client` (which wants ``#{window_id}`` — names can
    collide across windows, ids cannot, and a per-window option must be set
    against something unambiguous).

    Best-effort: missing tmux, missing session, or any subprocess error yields
    ``[]``, never an exception.
    """
    if shutil.which("tmux") is None:
        return []
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", session, "-F", field],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("list-windows({}, {}) failed: {}", session, field, exc)
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _shell_quote(value: str) -> str:
    """Quote a value for `export KEY=...`. Conservative single-quote wrap."""
    return "'" + value.replace("'", "'\\''") + "'"
