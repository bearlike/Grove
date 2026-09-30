"""Render the terminal status line for a Grove-launched Claude Code session.

Grove registers ``grove-agent-hook --statusline`` as the session's
``statusLine`` because that payload is the only place Claude Code states how
full the model's window is. Claude Code allows one ``statusLine``, so that
registration replaces the user's own for the pane Grove opened. This module is
what the pane shows in its place. The producer spools the payload for the
sidecar first and renders second, so a rendering failure can never cost the
context fold.

A render uses the standard library only and imports nothing from Grove,
because it runs on every render of every session. Its Grove-derived inputs are
two files read as plain JSON: the phase file, whose path Grove publishes in the
agent's environment, and a quota snapshot that a detached child refreshes
through the engine (:func:`refresh_quotas`), off the render path. One Python
implementation serves Linux, macOS and Windows. Every platform difference is
named in :class:`Platform`, so there is no shell or PowerShell copy that could
drift from this one.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, TypeGuard
from urllib.parse import urlparse

GIT_TIMEOUT_SECONDS: Final = 2.0
GLYPHS_ENV: Final = "GROVE_STATUSLINE_GLYPHS"
PHASE_FILE_ENV: Final = "GROVE_PHASE_FILE"
QUOTA_REFRESH_AFTER_SECONDS: Final = 120.0
"""How old the quota snapshot may get before a render asks for a new one."""
_REFRESH_FLAG: Final = "--refresh-quotas"
_PROVIDER_NAMES: Final = {"claude_code": "Claude", "codex": "Codex"}

_CONTEXT_CLASSES: Final = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_ANSI_RE: Final = re.compile(r"\x1b\[[0-9;]*m")


class Ansi:
    """SGR escapes by name. ``paint`` always resets, so a cell never bleeds."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    @classmethod
    def paint(cls, text: str, colour: str) -> str:
        return f"{colour}{text}{cls.RESET}"

    @classmethod
    def gauge(cls, pct: float, warn: float, crit: float, cold: str) -> str:
        """``cold`` below ``warn``, yellow to ``crit``, red at or above it."""
        if pct >= crit:
            return cls.RED
        if pct >= warn:
            return cls.YELLOW
        return cold


def visible_width(text: str) -> int:
    """Printed columns, not counting SGR escapes. A glyph counts as one column."""
    return len(_ANSI_RE.sub("", text))


class Glyphs:
    """Two vocabularies, chosen by ``GROVE_STATUSLINE_GLYPHS``.

    Nothing here can tell whether the terminal's font has Nerd Font glyphs,
    because the glyph is drawn by the emulator the human is using. So this is a
    switch, the same one the container decor script reads. The ASCII vocabulary
    puts the WORDS back rather than drawing a weaker icon, because a missing
    icon is only readable if it is replaced by the word it stood for. Glyphs
    are built from codepoints so a wrong source-file codepage cannot corrupt
    them without anyone noticing.
    """

    _NERD: Final = {
        "grove": 0xF0531,  # nf-md-tree
        "user": 0xF0004,
        "folder": 0xF024B,
        "branch": 0xF062C,
        "gauge": 0xF04C5,
        "load": 0xF035B,
        "clock": 0xF0954,
        "stash": 0xF03D7,
        "limit": 0xF0502,
    }
    _ASCII: Final = {
        "grove": "grove",
        "user": "",
        "folder": "",
        "branch": "git",
        "gauge": "ctx",
        "load": "load",
        "clock": "",
        "stash": "stash",
        "limit": "quota",
    }

    def __init__(self, ascii_only: bool) -> None:
        self.ascii_only = ascii_only

    @classmethod
    def from_env(cls, env: dict[str, str] | os._Environ[str]) -> Glyphs:
        return cls((env.get(GLYPHS_ENV) or "nerd").strip().lower() == "ascii")

    def icon(self, name: str, colour: str) -> str:
        """The glyph (or word) plus a separating space, or nothing at all."""
        text = self._ASCII[name] if self.ascii_only else chr(self._NERD[name])
        return f"{Ansi.paint(text, colour)} " if text else ""


# One separator on every row, dimmed so it recedes behind the values it divides.
SEP: Final = Ansi.paint(" · ", Ansi.DIM)


def context_usage(payload: dict[str, Any]) -> tuple[int, int] | None:
    """``(used, size)`` of the model's window, or ``None`` until it is measured.

    The one rule both the sidecar fold (``hook.py``) and this renderer read, so
    the terminal and every dashboard agree on the same number. Measured
    2026-09-14 on Claude Code 2.1.270: ``context_window_size`` is present from
    the first invocation, while ``current_usage`` is ``null`` until the first
    request completes and then carries the LAST request's four token classes,
    all of which occupy the window. Absent usage means "not measured yet",
    never zero. ``used_percentage`` is deliberately not read, because it is
    derived from the same numbers and rounding it here would give a second copy
    of one rule.
    """
    window = payload.get("context_window")
    if not isinstance(window, dict):
        return None
    size = window.get("context_window_size")
    usage = window.get("current_usage")
    if not _measured_int(size) or size <= 0 or not isinstance(usage, dict):
        return None
    counted = [usage[key] for key in _CONTEXT_CLASSES if _measured_int(usage.get(key))]
    if not counted:
        return None
    return sum(counted), size


def _measured_int(value: Any) -> TypeGuard[int]:
    """A reported non-negative integer. A ``bool`` is an ``int`` and is NOT one."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class Platform:
    """Everything that genuinely differs by operating system, and nothing else."""

    def __init__(self, env: dict[str, str] | os._Environ[str]) -> None:
        self._env = env

    def user_at_host(self) -> str:
        try:
            user = getpass.getuser()
        except Exception:  # no passwd entry in a slim container
            user = self._env.get("USERNAME") or self._env.get("USER") or ""
        host = socket.gethostname().split(".")[0]
        return "@".join(part for part in (user, host) if part)

    def home(self) -> str:
        return os.path.expanduser("~")

    def load_average(self) -> float | None:
        """One-minute load, or ``None`` on Windows, which has no equivalent.

        ``None`` drops the segment. A made-up number would be worse, because
        nobody could tell it was made up.
        """
        try:
            return os.getloadavg()[0]
        except (OSError, AttributeError):
            return None

    def terminal_width(self, fallback: int = 100) -> int:
        """Width of the TERMINAL, not of stdout.

        Claude Code reads this process's stdout through a pipe, so asking stdout
        answers the fallback for every terminal alike. Ask the other streams and
        the controlling terminal, which is what ``tput cols`` does.
        """
        columns = self._env.get("COLUMNS", "")
        if columns.isdigit() and int(columns) > 0:
            return int(columns)
        for stream in (sys.stderr, sys.stdin, sys.stdout):
            try:
                width = os.get_terminal_size(stream.fileno()).columns
            except (OSError, ValueError, AttributeError):
                continue
            if width > 0:
                return width
        try:
            with open(os.ctermid()) as tty:  # POSIX only; AttributeError on Windows
                return os.get_terminal_size(tty.fileno()).columns
        except (OSError, AttributeError):
            return fallback


@dataclass(frozen=True, slots=True)
class GitState:
    branch: str
    ahead: int = 0
    behind: int = 0
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0

    @classmethod
    def parse(cls, porcelain: str) -> GitState | None:
        """Read ``git status --porcelain=v2 --branch`` output.

        One process answers branch, upstream distance and all three file counts.
        This runs on every render, and the user's own status line spent three
        processes on what this reads from one. ``None`` when git printed no
        branch header, which means the directory is not a repository.
        """
        branch, oid = "", ""
        ahead = behind = staged = unstaged = untracked = 0
        for line in porcelain.splitlines():
            if line.startswith("# branch.head "):
                branch = line.removeprefix("# branch.head ")
            elif line.startswith("# branch.oid "):
                oid = line.removeprefix("# branch.oid ")
            elif line.startswith("# branch.ab "):
                for part in line.split()[2:]:
                    if part.startswith("+") and part[1:].isdigit():
                        ahead = int(part[1:])
                    elif part.startswith("-") and part[1:].isdigit():
                        behind = int(part[1:])
            elif line.startswith(("1 ", "2 ")):
                xy = line[2:4]
                staged += xy[0] != "."
                unstaged += xy[1] != "."
            elif line.startswith("u "):
                unstaged += 1  # an unresolved conflict is work still to do
            elif line.startswith("? "):
                untracked += 1
        if not branch:
            return None
        if branch == "(detached)":
            branch = oid[:7] or branch
        return cls(branch, ahead, behind, staged, unstaged, untracked)


class Git:
    """Repository facts for one directory, bounded so a stuck git cannot stall a render."""

    def read(self, cwd: str) -> GitState | None:
        if not cwd or not os.path.isdir(cwd):
            return None
        try:
            done = subprocess.run(
                # --no-optional-locks must come BEFORE the subcommand. A render
                # must never take the index lock a foreground `git add` needs.
                ["git", "--no-optional-locks", "status", "--porcelain=v2", "--branch"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return GitState.parse(done.stdout) if done.returncode == 0 else None


class Phase:
    """This agent's own reported task phase, read from the file Grove named.

    The path is published in the environment for exactly this agent, so the
    status line shows its own claim and never a directory neighbour's. It is
    read as plain JSON rather than through ``grove.core.phase``, because that
    import costs more than the whole render.
    """

    _COLOURS: Final = {"handoff": Ansi.GREEN, "verify": Ansi.CYAN, "deliver": Ansi.CYAN}

    def __init__(self, env: dict[str, str] | os._Environ[str]) -> None:
        self._path = env.get(PHASE_FILE_ENV) or ""

    def read(self) -> tuple[str, bool] | None:
        if not self._path:
            return None
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        phase = data.get("phase") if isinstance(data, dict) else None
        if not isinstance(phase, str) or not phase:
            return None
        return phase, bool(data.get("blocked"))

    @classmethod
    def colour(cls, phase: str) -> str:
        return cls._COLOURS.get(phase, Ansi.MAGENTA)


@dataclass(frozen=True, slots=True)
class QuotaWindow:
    label: str
    used_percent: float
    resets_at: float | None


@dataclass(frozen=True, slots=True)
class QuotaAccount:
    """One subscription as Grove's quota collector last rendered it."""

    provider: str
    label: str
    plan: str
    status: str
    windows: tuple[QuotaWindow, ...]

    @property
    def group(self) -> str:
        """Accounts of one known vendor share a cell; any other subscription is its own."""
        return (
            self.provider if self.provider in _PROVIDER_NAMES else f"{self.provider}:{self.label}"
        )

    @property
    def name(self) -> str:
        return _PROVIDER_NAMES.get(self.provider, self.label or self.provider)


class QuotaSnapshot:
    """The quota view a background refresh last wrote, and the trigger for the next one.

    The numbers come from ``UsageService.quotas()`` — the same view ``grove
    quota`` and ``GET /usage/quotas`` return — so the account selection, the
    stale labelling and the probe budget are Grove's collector's, never a
    second copy here. A render only READS the file. When the file is older than
    :data:`QUOTA_REFRESH_AFTER_SECONDS` it starts ONE detached refresh and
    carries on with what it has, because a status line must never wait on the
    network. A claim file coalesces every pane on the host onto that one
    refresh, and a metered provider's own TTL and cool-off still govern whether
    the refresh contacts anything at all.
    """

    def __init__(self, path: Path | None, *, spawn: Callable[[Path], None]) -> None:
        self._path = path
        self._spawn = spawn

    def accounts(self, now: float) -> list[QuotaAccount]:
        if self._path is None:
            return []
        self._refresh_if_stale(now)
        try:
            with open(self._path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        raw = data.get("accounts") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            return []
        return [account for entry in raw if (account := _account(entry, now)) is not None]

    def _refresh_if_stale(self, now: float) -> None:
        assert self._path is not None
        if now - _mtime(self._path) < QUOTA_REFRESH_AFTER_SECONDS:
            return
        claim = _claim(self._path)
        if now - _mtime(claim) < QUOTA_REFRESH_AFTER_SECONDS:
            return  # another pane already asked; a crashed refresh re-arms after one period
        try:
            claim.parent.mkdir(parents=True, exist_ok=True)
            claim.touch()
            self._spawn(self._path)
        except OSError:
            return


def _claim(path: Path) -> Path:
    return path.with_name(path.name + ".refreshing")


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _epoch(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()


def _account(entry: Any, now: float) -> QuotaAccount | None:
    """One wire ``BillingAccountView``, keeping only windows that are still open.

    A window whose reset has passed describes a period that is over, so its
    percentage says nothing about what is left now. It is dropped, and an
    account with nothing current left still renders its name and status,
    because hiding it would read as "no such subscription".
    """
    if not isinstance(entry, dict):
        return None
    windows = []
    for raw in entry.get("windows") or []:
        if not isinstance(raw, dict):
            continue
        used = raw.get("used_percent")
        if isinstance(used, bool) or not isinstance(used, int | float):
            continue  # not measured is not 0%
        resets = _epoch(raw.get("resets_at"))
        if resets is not None and resets <= now:
            continue
        windows.append(QuotaWindow(str(raw.get("label") or "?"), float(used), resets))
    subscription = entry.get("subscription")
    plan = ""
    if isinstance(subscription, dict):
        plan = str(subscription.get("label") or subscription.get("plan") or "")
    return QuotaAccount(
        provider=str(entry.get("provider") or ""),
        label=str(entry.get("label") or ""),
        plan=plan,
        status=str(entry.get("status") or ""),
        windows=tuple(windows),
    )


def spawn_refresh(path: Path) -> None:
    """Start ``python -m grove.statusline --refresh-quotas <path>`` fully detached."""
    # The flags exist only on Windows, hence getattr: the child must outlive a
    # render that returns in milliseconds, on every platform.
    detach: dict[str, Any] = (
        {
            "creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        }
        if sys.platform == "win32"
        else {"start_new_session": True}
    )
    subprocess.Popen(  # our own interpreter and module, no shell
        [sys.executable, "-m", "grove.statusline", _REFRESH_FLAG, str(path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **detach,
    )


def refresh_quotas(path: Path) -> None:
    """Write the collector's current quota view to ``path``, atomically.

    Runs in the detached child, never in a render, so the engine import (about
    a second) is paid off the status line's path.
    """
    from grove.core import load_config  # noqa: PLC0415
    from grove.core.registry import RepoRegistry  # noqa: PLC0415
    from grove.core.store import JsonWorkspaceStore  # noqa: PLC0415
    from grove.core.usage import UsageService  # noqa: PLC0415

    cfg = load_config(repo_root=None)
    registry = RepoRegistry(cfg=cfg, store=JsonWorkspaceStore(), config_loader=load_config)
    service = UsageService(cfg=cfg, registry=registry)
    try:
        payload = service.quotas().model_dump_json()
    finally:
        service.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    staged.write_text(payload, encoding="utf-8")
    os.replace(staged, path)
    _claim(path).unlink(missing_ok=True)


class StatusLine:
    """Assembles the rendered lines from injected collaborators.

    Everything that touches the machine arrives through the constructor, so a
    test can pin the payload, the clock, git and the platform and assert on the
    exact string.
    """

    def __init__(
        self,
        payload: dict[str, Any],
        *,
        env: dict[str, str] | os._Environ[str],
        platform: Platform,
        git: Git,
        phase: Phase,
        glyphs: Glyphs,
        quotas: QuotaSnapshot,
        now: float,
    ) -> None:
        self.payload = payload
        self.quotas = quotas
        self.env = env
        self.platform = platform
        self.git = git
        self.phase = phase
        self.glyphs = glyphs
        self.now = now

    # -- payload reads (a trust boundary: every key may be missing) -----------

    def _get(self, *path: str) -> Any:
        node: Any = self.payload
        for part in path:
            if not isinstance(node, dict):
                return None
            node = node.get(part)
        return node

    @property
    def cwd(self) -> str:
        found = self._get("workspace", "current_dir") or self._get("cwd")
        return found if isinstance(found, str) else ""

    # -- segments -------------------------------------------------------------

    def _grove(self) -> str:
        """The Grove badge and this agent's phase, which is what makes the pane recognisable."""
        badge = self.glyphs.icon("grove", Ansi.GREEN)
        claim = self.phase.read()
        if claim is None:
            return (badge or Ansi.paint("grove ", Ansi.GREEN)).rstrip()
        phase, blocked = claim
        out = badge + Ansi.paint(phase, Ansi.BOLD + Phase.colour(phase))
        if blocked:
            out += Ansi.paint(" blocked", Ansi.RED)
        return out

    def _via(self) -> str:
        """The gateway host, when the session is routed through one."""
        host = urlparse(self.env.get("ANTHROPIC_BASE_URL") or "").hostname or ""
        return Ansi.paint(f"via {host}", Ansi.DIM) if host else ""

    def _user(self) -> str:
        who = self.platform.user_at_host()
        return f"{self.glyphs.icon('user', Ansi.CYAN)}{who}" if who else ""

    def _folder(self) -> str:
        cwd = self.cwd
        if not cwd:
            return ""
        home = self.platform.home()
        if home and (cwd == home or cwd.startswith(home + os.sep)):
            cwd = "~" + cwd[len(home) :]
        return f"{self.glyphs.icon('folder', Ansi.BLUE)}{cwd}"

    def _git(self) -> str:
        state = self.git.read(self.cwd)
        if state is None:
            return ""
        out = f"{self.glyphs.icon('branch', Ansi.MAGENTA)}{state.branch}"
        if state.ahead:
            out += Ansi.paint(f" ↑{state.ahead}", Ansi.CYAN)
        if state.behind:
            out += Ansi.paint(f" ↓{state.behind}", Ansi.YELLOW)
        out += f" {Ansi.paint(f'+{state.staged}', Ansi.GREEN)}"
        out += f" {Ansi.paint(f'-{state.unstaged}', Ansi.RED)}"
        if state.untracked:
            out += " " + Ansi.paint(f"?{state.untracked}", Ansi.YELLOW)
        return out

    def _model(self) -> str:
        name = self._get("model", "display_name") or self._get("model", "id")
        if not isinstance(name, str) or not name:
            return ""
        effort = self._get("effort", "level")
        if isinstance(effort, str) and effort:
            return name + Ansi.paint(f" [{effort}]", Ansi.YELLOW)
        return name

    def _context(self) -> str:
        measured = context_usage(self.payload)
        if measured is None:
            return ""
        used, size = measured
        pct = used / size * 100
        body = f"{pct:.0f}% ({_tokens(used)} / {_tokens(size)})"
        colour = Ansi.gauge(pct, 50, 75, Ansi.CYAN)
        return f"{self.glyphs.icon('gauge', Ansi.CYAN)}{Ansi.paint(body, colour)}"

    def _load(self) -> str:
        value = self.platform.load_average()
        if value is None:
            return ""
        label = "" if self.glyphs.ascii_only else "load:"
        painted = Ansi.paint(f"{value:.2f}", Ansi.gauge(value, 2, 4, Ansi.GREEN))
        return f"{self.glyphs.icon('load', Ansi.GREEN)}{label}{painted}"

    def _clock(self) -> str:
        clock = datetime.fromtimestamp(self.now).strftime("%I:%M%p").lstrip("0")
        return f"{self.glyphs.icon('clock', Ansi.YELLOW)}{clock}"

    def _duration(self, epoch: float | None) -> str:
        """Time until ``epoch`` as ``Xd Yh``, ``Xh Ym`` or ``Ym``. Past a day, minutes are noise."""
        if epoch is None or epoch <= self.now:
            return ""
        left = int(epoch - self.now)
        days, hours, minutes = left // 86_400, left // 3_600 % 24, left // 60 % 60
        if days:
            return f"{days}d {hours}h"
        return f"{hours}h {minutes}m" if hours else f"{minutes}m"

    def _quota_cells(self) -> list[str]:
        """One cell per metered window, grouped by vendor, first cell of each vendor named.

        Several accounts of one vendor share a cell per window: the headline is
        their mean, and the per-account breakdown beside it takes the colour of
        the WORST account, because a mean can read green while one account is
        already refusing work. The reset shown is the soonest one.
        """
        groups: dict[str, list[QuotaAccount]] = {}
        for account in self.quotas.accounts(self.now):
            groups.setdefault(account.group, []).append(account)
        cells: list[str] = []
        for accounts in groups.values():
            head = accounts[0].name
            if len(accounts) > 1:
                head += f" x{len(accounts)}"
            elif accounts[0].plan and accounts[0].provider in _PROVIDER_NAMES:
                head += f" {accounts[0].plan}"
            windows = self._windows(accounts)
            status = {a.status for a in accounts} - {"ok"}
            flag = ""
            if "auth_expired" in status:
                flag = Ansi.paint(" REAUTH", Ansi.RED)
            elif "rate_limited" in status and not windows:
                flag = Ansi.paint(" rate-limited", Ansi.RED)
            elif status:
                flag = Ansi.paint(" (stale)", Ansi.YELLOW)
            label = Ansi.paint(f"{head} ", Ansi.DIM)
            if not windows:
                cells.append(Ansi.paint(head, Ansi.DIM) + (flag or Ansi.paint(" -", Ansi.DIM)))
                continue
            windows[0] = label + windows[0]
            windows[-1] += flag
            cells.extend(windows)
        if cells:
            cells[0] = self.glyphs.icon("limit", Ansi.RED) + cells[0]
        return cells

    def _windows(self, accounts: list[QuotaAccount]) -> list[str]:
        order: list[str] = []
        by_label: dict[str, list[QuotaWindow]] = {}
        for account in accounts:
            for window in account.windows:
                if window.label not in by_label:
                    order.append(window.label)
                by_label.setdefault(window.label, []).append(window)
        out = []
        for label in order:
            found = by_label[label]
            mean = sum(w.used_percent for w in found) / len(found)
            cell = Ansi.paint(f"{label}:{mean:.0f}%", Ansi.gauge(mean, 50, 80, Ansi.GREEN))
            if len(found) > 1:
                worst = max(w.used_percent for w in found)
                each = ",".join(f"{w.used_percent:.0f}" for w in found)
                cell += " " + Ansi.paint(f"[{each}]", Ansi.gauge(worst, 50, 80, Ansi.DIM))
            resets = [w.resets_at for w in found if w.resets_at is not None]
            when = self._duration(min(resets)) if resets else ""
            if when:
                cell += " " + Ansi.paint(f"(resets in {when})", Ansi.DIM)
            out.append(cell)
        return out

    # -- layout ---------------------------------------------------------------

    def render(self) -> str:
        width = self.platform.terminal_width()
        rows = [
            [self._grove(), self._user(), self._folder(), self._git()],
            [self._model(), self._context(), self._load(), self._clock()],
        ]
        # Drop absent segments BEFORE measuring, or the grid aligns around a hole.
        rows = [[cell for cell in row if cell] for row in rows]
        via = self._via()
        if via and rows[0]:
            rows[0][0] += " " + via
        lines = [_grid(rows, width), *_wrap(self._quota_cells(), width)]
        return "\n".join(line for line in lines if line) + "\n" + Ansi.paint("─" * width, Ansi.DIM)


def _tokens(count: int) -> str:
    """Truncate, never round.

    A 983,616-token window shown as 984K would claim room that does not exist.
    """
    if count >= 1_000_000:
        return f"{count // 100_000 / 10:.1f}M"
    if count >= 1_000:
        return f"{count // 1_000}K"
    return str(count)


def _grid(rows: list[list[str]], limit: int) -> str:
    """Pad shared columns so the separators line up as vertical rules, or pad nothing.

    Alignment is all or nothing. Padding one column widens every row, and a
    wrapped status line splits SGR escapes across the break and renders as
    garbage. So the padded grid is measured, and it is abandoned whole if any
    row would overflow, because a half-aligned grid looks like a mistake. The
    last cell of a row is never padded, since trailing spaces only push the
    line toward the edge.
    """
    rows = [row for row in rows if row]
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    widths = [max(visible_width(row[i]) for row in rows if i < len(row)) for i in range(columns)]
    shared = [sum(1 for row in rows if i < len(row)) > 1 for i in range(columns)]

    def join(row: list[str], pad: bool) -> str:
        cells = []
        for i, cell in enumerate(row):
            gap = widths[i] - visible_width(cell)
            padded = pad and shared[i] and gap > 0 and i < len(row) - 1
            cells.append(cell + " " * gap if padded else cell)
        return SEP.join(cells)

    padded = [join(row, True) for row in rows]
    if all(visible_width(line) <= limit for line in padded):
        return "\n".join(padded)
    return "\n".join(join(row, False) for row in rows)


def _wrap(cells: list[str], limit: int) -> list[str]:
    """Pack cells into as many rows as the width needs, never splitting a cell.

    Several subscriptions do not fit one row on an ordinary terminal, and a
    wrapped status line breaks its escapes, so the row breaks between cells
    instead. A cell wider than the terminal gets a row to itself.
    """
    rows: list[str] = []
    current = ""
    for cell in cells:
        joined = f"{current}{SEP}{cell}" if current else cell
        if current and visible_width(joined) > limit:
            rows.append(current)
            current = cell
        else:
            current = joined
    if current:
        rows.append(current)
    return rows


def render(
    payload: dict[str, Any],
    *,
    env: dict[str, str] | os._Environ[str] | None = None,
    quota_path: Path | None = None,
) -> str:
    """The status line for one payload, wired to the real machine."""
    environ = os.environ if env is None else env
    return StatusLine(
        payload,
        env=environ,
        platform=Platform(environ),
        git=Git(),
        phase=Phase(environ),
        glyphs=Glyphs.from_env(environ),
        quotas=QuotaSnapshot(quota_path, spawn=spawn_refresh),
        now=time.time(),
    ).render()


def emit(text: str) -> None:
    """Write UTF-8 whatever the console codepage says.

    On Windows a piped stdout defaults to the ANSI codepage, which cannot
    encode a Nerd Font glyph, so a text-mode write raises instead of drawing.
    """
    stream = getattr(sys.stdout, "buffer", None)
    if stream is None:
        sys.stdout.write(text)
        return
    stream.write(text.encode("utf-8", errors="replace"))
    stream.flush()


if __name__ == "__main__":
    # The detached quota refresh. Any failure leaves the old snapshot in place,
    # and the claim file lets the next render retry after one period.
    if len(sys.argv) == 3 and sys.argv[1] == _REFRESH_FLAG:
        refresh_quotas(Path(sys.argv[2]))
