"""Where a config section's environment values come from, and how they are read.

One question, one class. :class:`EnvSource` resolves the two cascaded
`env_file` / `env_command` knobs of ANY :class:`~grove.core.config.EnvSourceConfig`
section into a mapping — `container` (the workspace's own environment, handed to
the devcontainer CLI by :meth:`EnvSource.secrets_file`) and `tickets` (where a
tracker's API token is read from) today. It knows nothing of Docker, the
provisioner, the manager, or a tracker; the caller applies what it describes.

**Resolution is deliberately not cached anywhere.** The whole reason this seam
exists is that the consuming process is long-lived: a value that appeared after
it started — written by a workspace init script, rotated by a secret manager —
must be visible at the next read, and a cache in a daemon would both hold
secrets in memory for days and keep serving the stale one. The cost is pushed
into the contract instead: an ``env_command`` must be idempotent and cheap.

The single invariant the whole module is written around: **a value never
reaches a log line, an error message, or a repr.** Keys and counts are
diagnosable, values are not — everything here is a secret until proven
otherwise, and the one channel that legitimately carries them (a command's
STDOUT) is therefore excluded from every message this module produces.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from loguru import logger

from grove.core import paths
from grove.core.config import EnvSourceConfig
from grove.core.errors import EnvSourceError


@dataclass(frozen=True, slots=True, repr=False)
class EnvSource:
    """Environment variables resolved for one config section, at one moment."""

    values: Mapping[str, str]

    #: The only escapes a double-quoted value processes. Deliberately short:
    #: a dotenv file is a secret store, not a shell script, and every extra
    #: escape is another way for a literal backslash in a token to be eaten.
    _ESCAPES: ClassVar[Mapping[str, str]] = {
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "\\": "\\",
        '"': '"',
    }

    #: A UTF-8 BOM survives ``encoding="utf-8"`` (only ``utf-8-sig`` eats it)
    #: and would otherwise become part of the first key — a `KEY` that looks
    #: identical in every diff and every terminal, and matches nothing.
    _BOM: ClassVar[str] = "\ufeff"

    #: Bound on how much of a failed command's STDERR is quoted back. A command
    #: that fails by dumping its whole help text should not push the actual
    #: reason off the top of the user's terminal.
    _STDERR_TAIL_CHARS: ClassVar[int] = 500

    def __repr__(self) -> str:
        """Count only — never the mapping.

        The dataclass default would print every key AND value, and a repr is
        reached by paths nobody audits: an incidental f-string, a `logger`
        call that interpolates the object, a traceback frame dump (loguru's
        `diagnose=True` renders locals). Making the repr safe is cheaper than
        auditing every future call site.
        """
        return f"EnvSource({len(self.values)} vars)"

    # ─── resolution (the side-effect edge) ──────────────────────────────────

    @classmethod
    def resolve(cls, cfg: EnvSourceConfig, *, repo_root: Path, timeout: float = 30.0) -> EnvSource:
        """Resolve one section's env source, right now, from the cascaded config.

        Neither knob set — the default — is the cheap path: no file read, no
        subprocess, an empty mapping. The two knobs are mutually exclusive and
        the config layer enforces that; the check is repeated here for the same
        reason `tmux.run_init_script` keeps its own `inline`/`path` check —
        this class is also constructed from models assembled in tests and by
        callers that never went through validation.

        Every message names the section (``cfg.SECTION``) rather than a
        hard-coded ``container``, so a user reading the error knows which block
        of their config to open.
        """
        if cfg.env_file and cfg.env_command:
            raise EnvSourceError(
                f"{cfg.SECTION}.env_file and {cfg.SECTION}.env_command are mutually "
                "exclusive; set exactly one"
            )
        if cfg.env_file:
            return cls._from_file(cfg.env_file, repo_root=repo_root, section=cfg.SECTION)
        if cfg.env_command:
            return cls._from_command(
                cfg.env_command, repo_root=repo_root, timeout=timeout, section=cfg.SECTION
            )
        return cls({})

    @classmethod
    def _from_file(cls, raw: str, *, repo_root: Path, section: str) -> EnvSource:
        """Read a configured dotenv file. A missing one is fatal, never skipped.

        The user named this file explicitly, so "it isn't there" is a config
        error, not a reason to launch an agent with a silently empty
        environment — that failure would surface much later as the tool
        reporting a missing credential, from a workspace that looked healthy.
        """
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = repo_root / path
        if not path.is_file():
            raise EnvSourceError(
                f"{section}.env_file {path} does not exist (or is not a file); it was "
                "configured explicitly, so it is not silently treated as empty"
            )
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise EnvSourceError(f"{section}.env_file {path} could not be read: {exc}") from exc
        values = cls.parse(text)
        cls._log_resolved(section=section, source=str(path), values=values)
        return cls(values)

    @classmethod
    def _from_command(
        cls, command: str, *, repo_root: Path, timeout: float, section: str
    ) -> EnvSource:
        """Run a host command and parse its STDOUT as dotenv.

        The command runs with ``shell=False`` over ``shlex.split``: a shell
        would turn a config string into a second injection surface (command
        substitution, redirection, chaining) for a value that Grove already
        treats as an argv everywhere else. A user who genuinely wants a
        pipeline writes ``bash -c '...'`` and owns that choice explicitly.

        **Every failure below names the command IN FULL and never bounds it.**
        Deliberate, and the two strings are not the same kind of thing: the
        command is config the operator wrote, and an operator staring at a
        timeout needs to know *which* command timed out — truncating the one
        identifying detail to a guessed length would make exactly the
        hardest failure the least diagnosable. It is also not a secret channel
        by construction: an argv is already visible to every process on the
        host through ``ps``/``/proc``, so a token inlined there was public
        before Grove ever printed it (the fix for that is a script or the
        environment, not a shorter error message). STDOUT is the opposite —
        it is the resolved-value channel and exists nowhere else — so it is
        the one that is never quoted back, on any path. Only the command's
        STDERR is capped (:meth:`_tail`), since that one is unbounded output
        from a program Grove does not control.
        """
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            raise EnvSourceError(
                f"{section}.env_command {command!r} is not a parseable command: {exc}"
            ) from exc
        if not argv:
            raise EnvSourceError(f"{section}.env_command is set but parses to an empty command")
        try:
            result = subprocess.run(
                argv,
                shell=False,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=repo_root,
                check=False,
            )
        except subprocess.TimeoutExpired:
            # Not chained: `TimeoutExpired` carries the partially-read STDOUT on
            # the exception object, and STDOUT is the value channel — a
            # diagnosing renderer that shows the cause's attributes would print
            # secrets. The message below says everything the user needs.
            raise EnvSourceError(
                f"{section}.env_command {command!r} timed out after {timeout:g}s"
            ) from None
        except OSError as exc:
            raise EnvSourceError(
                f"{section}.env_command {command!r} could not be run: {exc}"
            ) from exc
        if result.returncode != 0:
            # STDERR only. STDOUT is where the secrets are, so it is never
            # quoted back — not even on the failure path, where a half-written
            # dump is exactly what a caller would be tempted to show.
            raise EnvSourceError(
                f"{section}.env_command {command!r} exited {result.returncode}: "
                f"{cls._tail(result.stderr)}"
            )
        values = cls.parse(result.stdout)
        cls._log_resolved(section=section, source=command, values=values)
        return cls(values)

    @classmethod
    def _tail(cls, stderr: str) -> str:
        """A bounded, single-line-ish tail of a failed command's STDERR."""
        text = stderr.strip()
        if not text:
            return "(no stderr)"
        if len(text) <= cls._STDERR_TAIL_CHARS:
            return text
        return f"...{text[-cls._STDERR_TAIL_CHARS :]}"

    @staticmethod
    def _log_resolved(*, section: str, source: str, values: Mapping[str, str]) -> None:
        """One INFO line naming the section, the source and the COUNT; keys at DEBUG."""
        logger.info("{} env: resolved {} variable(s) from {}", section, len(values), source)
        if values:
            logger.debug("{} env: keys {}", section, ", ".join(sorted(values)))

    # ─── parsing (pure) ─────────────────────────────────────────────────────

    @staticmethod
    def parse(text: str) -> dict[str, str]:
        """Parse dotenv text literally. **No ``${VAR}`` interpolation, ever.**

        Hand-written rather than delegated to `python-dotenv`, and not merely to
        avoid a dependency: python-dotenv performs POSIX ``${VAR}`` expansion by
        default, which silently corrupts any secret containing a ``$`` — a
        generated password, a bcrypt hash, a JWT with a ``$`` in it — turning it
        into a shorter, wrong value with no error anywhere. A secrets parser
        must be literal, so this one is.

        The grammar: `KEY=VALUE` split on the FIRST ``=``; an optional `export`
        prefix; `#` comments (full-line anywhere, inline only after whitespace
        and only in an unquoted value); single quotes literal; double quotes
        with the five escapes in :attr:`_ESCAPES`; last assignment wins.

        A malformed line is **ignored, not fatal** — a stray line in a secret
        dump (a banner, a warning the producer wrote to STDOUT) must not cost
        the user their workspace. It is logged at DEBUG by line NUMBER, never
        by content.
        """
        values: dict[str, str] = {}
        for number, raw in enumerate(text.split("\n"), start=1):
            line = raw.removesuffix("\r")
            if number == 1:
                line = line.removeprefix(EnvSource._BOM)
            line = EnvSource._strip_export(line.strip())
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            key = key.strip()
            if not sep or not key:
                logger.debug("env source: ignoring unparseable line {}", number)
                continue
            values[key] = EnvSource._unquote(value.strip())
        return values

    @staticmethod
    def _strip_export(line: str) -> str:
        """Drop a leading ``export`` and the whitespace after it."""
        keyword = "export"
        rest = line[len(keyword) :]
        if line.startswith(keyword) and rest[:1].isspace():
            return rest.lstrip()
        return line

    @staticmethod
    def _unquote(value: str) -> str:
        """Resolve one already-trimmed value's quoting.

        Quoting is what says "this is exactly my value": a quoted value keeps
        its ``#``, its trailing spaces and (single-quoted) its backslashes
        verbatim, which is the only way to express a secret that contains them.
        """
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            inner = value[1:-1]
            return inner if value[0] == "'" else EnvSource._unescape(inner)
        return EnvSource._strip_inline_comment(value)

    @staticmethod
    def _unescape(inner: str) -> str:
        """Apply the :attr:`_ESCAPES` table; leave every other backslash alone.

        An unknown escape stays literal (``\\d`` is ``\\d``) rather than losing
        its backslash — a password ending in one would otherwise silently
        change, and a parser that quietly rewrites secrets is worse than one
        that refuses them.
        """
        out: list[str] = []
        index = 0
        while index < len(inner):
            char = inner[index]
            replacement = (
                EnvSource._ESCAPES.get(inner[index + 1])
                if char == "\\" and index + 1 < len(inner)
                else None
            )
            if replacement is None:
                out.append(char)
                index += 1
                continue
            out.append(replacement)
            index += 2
        return "".join(out)

    @staticmethod
    def _strip_inline_comment(value: str) -> str:
        """Drop ` #`-onwards from an UNQUOTED value.

        Whitespace-anchored so a value that merely contains a ``#`` (a fragment
        in a URL, a literal in a token) survives; a user who needs a trailing
        `` #`` quotes the value.
        """
        for index, char in enumerate(value):
            if char == "#" and index > 0 and value[index - 1].isspace():
                return value[:index].strip()
        return value

    # ─── the devcontainer CLI handoff ───────────────────────────────────────

    @contextmanager
    def secrets_file(self, workspace_id: str) -> Iterator[Path | None]:
        """Materialize these values as a `devcontainer up --secrets-file` file.

        **The file is a JSON object of string key/value pairs (parsed as JSONC),
        NOT a dotenv file** — verified against `@devcontainers/cli` 0.88.0's
        bundled source. The flag's name invites the opposite assumption and the
        CLI's failure mode for a dotenv file is an unhelpful parse error, so
        this is the fact worth keeping. Also
        verified: the CLI masks these values as ``********`` in its own output,
        so Grove's provision log stays safe to keep and to show.

        The file lives under Grove's state directory
        (:func:`grove.core.paths.container_secrets_path`), **outside every
        worktree by construction**: a worktree is a git checkout that is itself
        bind-mounted into the container, so a secrets file written there is one
        ``git add -A`` away from being committed — and the agent inside the
        container can read it.

        Yields ``None`` for an empty mapping so the caller passes no flag at all
        rather than handing the CLI an empty file. The path is removed in a
        ``finally``, so an exception mid-`up` still takes the secrets with it.
        """
        if not self.values:
            yield None
            return
        path = paths.container_secrets_path(workspace_id)
        paths.ensure_dir(path.parent)
        payload = json.dumps(dict(self.values), indent=2, sort_keys=True)
        try:
            # Unlink first, then create with the mode: `O_CREAT` does NOT apply
            # a mode to an existing file, so a leftover from a crashed run would
            # keep whatever permissions it had.
            path.unlink(missing_ok=True)
            # Created 0600 by `os.open`, never write-then-chmod: a chmod leaves
            # a window — however short — in which the file is world-readable
            # with the secrets already in it.
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(payload)
            yield path
        finally:
            path.unlink(missing_ok=True)
