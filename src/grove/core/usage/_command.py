"""Which executable led a shell tool call — the one question this module answers.

`usage_events.target` is the file a tool touched for every tool that names one.
A shell call names none, so the column sat NULL for every `Bash`/`exec_command`
row and the question *which processes eat the most agent time* had no evidence
behind it. This module supplies the missing value: the **leading top-level
executable** of the command string, normalized.

Four measurements over 111,578 real commands from this host's Claude and Codex
corpora decided every choice below. Re-open one only with a new measurement.

**Naive splitting is not fit to ship.** 59.6% of real commands contain a pipe,
56.0% an `&&`/`||`/`;`, 27.0% are multi-line, and **34.3% carry an operator
character inside a quoted span**. Hand-checked against 100 stratified-random
commands, naive first-token attribution was ~31% correct and a careful `shlex`
segmentation ~79% — and the `shlex` pass *never signals failure*, so its 21%
is indistinguishable from its 79%. `bashlex` (a real bash grammar) was
effectively 100% correct wherever it parsed and raised loudly otherwise.

**Heredocs go straight to the fallback, before bashlex is consulted.** bashlex
cannot parse a QUOTED heredoc delimiter (`<<'EOF'`) — 99.2% of this corpus's
heredoc style — so 99.0% of the 10,627 heredoc-bearing commands raise
`ParsingError` there. Trying first buys nothing and spends the risk budget of
the one boundary in this file that has been observed to HANG.

**The fallback strips heredoc BODIES before it looks for an operator.** A
markdown table inside a heredoc is a wall of `|` characters, and a scan that
treats them as pipe operators invents executables out of prose: measured, the
distinct-executable count was **51,622 before body stripping and 4,003 after**.
If a change here ever produces a five-figure executable count, this is why.

**The whole call's duration is attributed to the LEADING command only**, which
is the one rule under which the attributed total equals real wall clock.
Crediting every executable in a call inflated the attributed total to
2,384,356 s against a true 782,272 s — **3.05x**. Verified here from the other
end, over 93,376 correlated Claude calls: **747,432 s attributed plus 3,139 s
unattributed equals the 750,571 s measured**. The known cost is that
`find … | xargs pylint` credits `find`; that is real, smaller than the
alternative, and directionally right because producers dominate cost. It is
also why the number must be labelled *"time in Bash calls led by X"* and never
*"time spent in X"*.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from types import FrameType
from typing import Any, Final, Literal

from loguru import logger

from grove.core.config import UsageCommandsConfig

try:  # pragma: no cover - exercised by the degrade path, not by a branch test
    import bashlex  # type: ignore[import-untyped]
    from bashlex import errors as _bashlex_errors

    _BASHLEX_ERRORS: tuple[type[Exception], ...] = (
        _bashlex_errors.ParsingError,
        NotImplementedError,
    )
except ImportError:  # pragma: no cover - only on a lean/older install
    # A missing distribution degrades this module to its own scan rather than
    # breaking `import grove.core`, because `usage/projector.py` is reached
    # from the daemon's startup path.
    bashlex = None
    _BASHLEX_ERRORS = ()
    logger.debug("bashlex unavailable; shell-command attribution uses the fallback scan")


SHELL_TOOL_NAMES: Final = frozenset(
    {
        # Claude Code. `BashOutput`/`KillShell` are deliberately absent: they
        # address an already-running background job by id and carry no command,
        # so counting them as shell calls would only inflate `unattributed`.
        "Bash",
        # Codex. `exec_command` is the current shape (10,872+ real calls);
        # `shell`/`local_shell` are the older function names, kept because a
        # historical rollout on disk still carries them.
        "exec_command",
        "shell",
        "local_shell",
    }
)
"""Tool names whose call IS a shell command.

Shared by the projector (which fills `target`) and the ranking query (which
selects the rows), so the write side and the read side cannot disagree about
what counts as a shell call.
"""

_COMMAND_KEYS: Final = ("command", "cmd")
"""Where each harness puts the command string. Claude's `Bash` uses `command`
(a string); Codex's `exec_command` uses `cmd`. A list value (`["bash", "-lc",
"…"]`, the OpenAI local-shell shape) is joined back into one line."""

_BACKGROUND_KEY: Final = "run_in_background"

# ---------------------------------------------------------------------------
# Bash's own grammar. Not config: these are facts about bash and about the
# POSIX tools named, not Grove policy. Getting one wrong FABRICATES an
# executable name rather than degrading, which is the argument for keeping
# them beside the code that reads them and pinned by test.
# ---------------------------------------------------------------------------

_BUILTINS: Final = frozenset(
    {
        # Spawn no process, so they must never be billed for a call's duration.
        ":",
        ".",
        "alias",
        "bg",
        "bind",
        "break",
        "builtin",
        "caller",
        "cd",
        "compgen",
        "complete",
        "continue",
        "declare",
        "dirs",
        "disown",
        "echo",
        "enable",
        "eval",
        "export",
        "false",
        "fc",
        "fg",
        "getopts",
        "hash",
        "help",
        "history",
        "jobs",
        "let",
        "local",
        "logout",
        "mapfile",
        "popd",
        "printf",
        "pushd",
        "pwd",
        "read",
        "readarray",
        "readonly",
        "return",
        "set",
        "shift",
        "shopt",
        "source",
        "suspend",
        "test",
        "trap",
        "true",
        "type",
        "typeset",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "wait",
        # The test builtins. `[` alone accounted for 763 real "executables"
        # before it was listed here; `[ -f x ] && make` must bill `make`.
        "[",
        "[[",
    }
)

_SKIP_KEYWORDS: Final = frozenset(
    # A real command follows these on the same segment, so skip the word and
    # keep looking: `if grep …`, `while test …`, `do pylint …`, `! cmp …`.
    {"if", "elif", "then", "else", "do", "while", "until", "!", "{", "}", "coproc"}
)

_STOP_KEYWORDS: Final = frozenset(
    # What follows these is a variable name or a pattern, never a command, so
    # the segment is abandoned rather than mined: `for f in *.py` must not bill
    # an executable called `f`.
    {"for", "select", "case", "in", "esac", "fi", "done", "function", "time"}
)


@dataclass(frozen=True, slots=True)
class _Prefix:
    """A command that RUNS another command, plus enough of its own flag grammar
    to find where its arguments stop.

    A blanket "skip one word after the prefix" is what produces `-I{}` and `-u`
    as executables — several hundred occurrences each in the real corpus. The
    flag sets below are the minimum that removes that artifact.
    """

    value_flags: frozenset[str] = frozenset()
    """Flags whose VALUE is the next word (`-u bob`). An attached short value
    (`-I{}`) and a `--flag=value` need no extra skip and are detected by shape."""

    positionals: int = 0
    """Leading non-flag words that are still the prefix's own arguments —
    `timeout 30 pytest` has one (the duration)."""

    assignments: bool = False
    """Whether `VAR=value` words before the command belong to the prefix
    (`env FOO=1 pytest`, `sudo FOO=1 pytest`)."""


_PREFIXES: Final[dict[str, _Prefix]] = {
    "command": _Prefix(),
    "builtin": _Prefix(),
    "exec": _Prefix(value_flags=frozenset({"-a"})),
    "nohup": _Prefix(),
    "setsid": _Prefix(),
    "sudo": _Prefix(
        value_flags=frozenset(
            {"-u", "-g", "-p", "-C", "-h", "-r", "-t", "-U", "--user", "--group", "--prompt"}
        ),
        assignments=True,
    ),
    "doas": _Prefix(value_flags=frozenset({"-u", "-C"})),
    "env": _Prefix(
        value_flags=frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
        assignments=True,
    ),
    "nice": _Prefix(value_flags=frozenset({"-n", "--adjustment"})),
    "ionice": _Prefix(value_flags=frozenset({"-c", "-n", "-p", "-P", "-u"})),
    "stdbuf": _Prefix(value_flags=frozenset({"-i", "-o", "-e", "--input", "--output", "--error"})),
    "timeout": _Prefix(
        value_flags=frozenset({"-s", "--signal", "-k", "--kill-after"}), positionals=1
    ),
    "xargs": _Prefix(
        value_flags=frozenset(
            {
                "-I",
                "-i",
                "-n",
                "-L",
                "-P",
                "-s",
                "-E",
                "-d",
                "-a",
                "--replace",
                "--max-args",
                "--max-lines",
                "--max-procs",
                "--max-chars",
                "--eof",
                "--delimiter",
                "--arg-file",
                "--process-slot-var",
            }
        )
    ),
    # `time` is a bash KEYWORD, handled in `_STOP_KEYWORDS` above, but
    # `/usr/bin/time` is a real binary that takes flags of its own.
    "time": _Prefix(value_flags=frozenset({"-o", "-f", "--output", "--format"})),
}

_ASSIGNMENT: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")

_IMPLAUSIBLE_NAME: Final = re.compile(r"""[\s$*?"'`#\\|;<>&()\[\]{}]""")
"""Characters no executable name can contain.

The guard exists because a word that reaches the resolver is not necessarily a
command name: a `case` pattern (`*"status":"failed"*`), a variable-expanded
command (`$AAPT build`), a stray `-z` from a test expression. Publishing those
as executables is exactly the fabrication this module exists to avoid, so a
word carrying any of these — or leading with `-` — abandons its segment
instead. Measured on the real corpus: 40 variable-expanded and ~15
pattern-shaped names, all now honestly unattributed."""
_HEREDOC_OPERATOR: Final = re.compile(
    r"(?<!<)<<(?!<)-?\s*(?:(['\"])([^'\"]*)\1|([A-Za-z_][A-Za-z0-9_]*))"
)
"""A heredoc opener — and deliberately NOT a `<<<` herestring.

Both guards are load-bearing and each closes a different half of the same bug.
Without them, `grep -q foo <<< "$output"` matched at the SECOND `<`: the regex
read `<< "$output"` as a quoted heredoc opener whose delimiter is `$output`,
`_strip_heredocs` then scanned forward for a terminator line that can never
exist, and every remaining line of the command was swallowed. A multi-line
command whose first line used a herestring therefore lost its real leading
executable entirely and was published as `unattributed`.

**The prevalence of `<<<` is not the size of the bug, and conflating the two
is how this fix nearly shipped with a number 11x too flattering.** Over 1,279
real `Bash` calls from 60 on-host Claude transcripts, 11 (0.86%) contain `<<<`
— but re-running the whole corpus through the OLD regex and the new one and
DIFFING the attributions moved exactly **one** command (unattributed 18 → 17).
The rest already attributed correctly, because a herestring only swallows the
command when the bogus delimiter it invents fails to match any following line
AND there is a later segment carrying the real leading executable. Counting the
commands that merely CONTAIN the construct measures exposure, not damage; only
the diff measures damage.

A herestring genuinely has no body to strip: `<<< word` feeds one expanded word
to stdin and consumes no following lines, so the correct handling is to ignore
it and let the ordinary word scan proceed."""

_TWO_CHAR_OPERATORS: Final = frozenset({"&&", "||", ";;", "|&", ";&"})
_TWO_CHAR_REDIRECTS: Final = frozenset({">>", "<&", ">&", "&>", "<>"})
_SEGMENT_OPERATORS: Final = frozenset({"&&", "||", ";", ";;", "|", "|&", "&", "\n", "(", ")"})


@dataclass(frozen=True, slots=True)
class _Token:
    kind: Literal["word", "op", "redirect"]
    text: str


class _ParseBudgetExceeded(Exception):
    """The bashlex parse ran past its wall-clock budget and was aborted."""


class LeadingCommand:
    """Resolves the normalized leading executable of a shell command string.

    Stateless apart from its configuration, so one instance is built per
    projector and reused across every session in a refresh.
    """

    MAX_LENGTH: Final = 10_000
    """Commands longer than this skip bashlex entirely.

    bashlex is a pure-Python yacc parser with no input bound of its own, and it
    has been reported HANGING on a real command hard enough to need `kill -9`.
    The cap is the cheap half of the guard; `PARSE_BUDGET_MS` is the half that
    actually bounds it. Sized well above real commands (the whole 112k-command
    corpus fits under it) so the cap alone essentially never fires — its job is
    to keep the *worst case* of the budget below a few seconds, since traced
    event count grows with input length.
    """

    PARSE_BUDGET_MS: Final = 250
    """Wall-clock budget for one bashlex parse, enforced by a tracing watchdog.

    NOT a config knob: it is the guard that keeps a third-party parser from
    taking a daemon thread with it, and an operator setting it to zero effect
    is a foot-gun with no upside. 250 ms is ~700x the measured median parse
    (343 us), so an exception here means a pathological input, never a slow
    host.
    """

    _CLOCK_EVERY: Final = 8192
    """Traced events between wall-clock reads.

    Tracing is the cost of the guard (measured 343 us raw against 1.6 ms
    traced), and reading the clock on every line event is a third of that
    again for no extra safety — the budget is a wall-clock bound either way,
    just checked at a coarse granularity.
    """

    def __init__(self, cfg: UsageCommandsConfig) -> None:
        self._cfg = cfg
        self._version_suffix = re.compile(cfg.version_suffix_pattern)

    # -- public surface ----------------------------------------------------

    @staticmethod
    def command_text(tool_input: dict[str, Any] | None) -> str | None:
        """The command string a shell tool call carried, or `None`."""
        if not isinstance(tool_input, dict):
            return None
        for key in _COMMAND_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, list):
                joined = " ".join(part for part in value if isinstance(part, str))
                if joined.strip():
                    return joined
        return None

    @staticmethod
    def in_background(tool_input: dict[str, Any] | None) -> bool:
        """Whether the harness was asked to detach this call.

        ~2.7% of real calls set it. The tool result then returns near-instantly
        with a handle, so the measured duration is the LAUNCH, not the work —
        which is why the count rides the wire beside the ranking rather than
        being quietly folded into it.
        """
        return bool(isinstance(tool_input, dict) and tool_input.get(_BACKGROUND_KEY))

    def of(self, command: str | None) -> str | None:
        """The normalized leading executable, or `None` when none is resolvable.

        `None` is an honest answer — a command that is only builtins, only a
        loop header, or that neither road could read — and the wire reports it
        as `unattributed` rather than dropping the call.
        """
        if not command or not command.strip():
            return None
        words = self._leading_words(command)
        raw = self._executable(words) if words else None
        return self._normalize(raw) if raw else None

    # -- the two roads -----------------------------------------------------

    def _leading_words(self, command: str) -> list[str] | None:
        """The first simple command's words, by whichever road can read them.

        A heredoc routes straight to the fallback: bashlex fails 99.0% of them,
        so trying first costs a guaranteed exception and one more exposure to
        the hang.
        """
        if _HEREDOC_OPERATOR.search(command) is None:
            parsed = self._bashlex_words(command)
            if parsed is not None:
                return parsed
        return self._scanned_words(command)

    def _bashlex_words(self, command: str) -> list[str] | None:
        """Every simple command's words in source order, or `None` if unparsed."""
        if bashlex is None or len(command) > self.MAX_LENGTH:
            return None
        try:
            trees = self._guarded_parse(command)
        except _BASHLEX_ERRORS:
            return None
        except _ParseBudgetExceeded:
            logger.warning(
                "bashlex exceeded its {} ms budget on a {}-char command; using the fallback scan",
                self.PARSE_BUDGET_MS,
                len(command),
            )
            return None
        except Exception as exc:  # pragma: no cover - a third-party parser edge
            logger.debug("bashlex degraded ({}); using the fallback scan", type(exc).__name__)
            return None
        words: list[str] = []
        for tree in trees:
            _collect_command_words(tree, words)
        return words

    def _guarded_parse(self, command: str) -> Any:
        """`bashlex.parse` under a wall-clock watchdog.

        The watchdog is `sys.settrace` on the CALLING thread, which is the only
        mechanism that actually stops pure-Python work in place. A signal alarm
        is main-thread-only and the projector runs in the daemon's executor;
        handing the parse to a worker thread bounds the CALLER but not the
        work, since a thread nothing can kill keeps a core busy for the life of
        the process. Returning the tracer from itself enables per-LINE events,
        so a loop containing no calls is bounded too.

        **What this does NOT bound, stated because a guard nobody can see the
        edge of is worse than none: a hang inside a single C-level call** — a
        catastrophically backtracking regex, say — executes no Python event and
        no tracer can interrupt it. `MAX_LENGTH` is the only thing standing
        between that case and the process.
        """
        deadline = time.monotonic() + self.PARSE_BUDGET_MS / 1000
        countdown = self._CLOCK_EVERY

        def watchdog(frame: FrameType, event: str, arg: Any) -> Any:
            del frame, event, arg
            nonlocal countdown
            countdown -= 1
            if countdown <= 0:
                if time.monotonic() > deadline:
                    raise _ParseBudgetExceeded
                countdown = self._CLOCK_EVERY
            return watchdog

        previous = sys.gettrace()
        sys.settrace(watchdog)
        try:
            return bashlex.parse(command)
        finally:
            sys.settrace(previous)

    def _scanned_words(self, command: str) -> list[str]:
        """Every simple command's words, by a quote-aware scan of the raw text.

        Heredoc bodies are removed first — see the module docstring on the
        51,622-vs-4,003 measurement.
        """
        tokens = _scan(_strip_heredocs(command))
        words: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token.kind == "redirect":
                index += 2  # the operator and its target
                continue
            if token.kind == "op":
                words.append(_SEGMENT_BREAK)
                index += 1
                continue
            words.append(token.text)
            index += 1
        return words

    # -- resolution --------------------------------------------------------

    def _executable(self, words: list[str]) -> str | None:
        """The first word that names a process, walking segments in order."""
        index = 0
        while index < len(words):
            word = words[index]
            if word == _SEGMENT_BREAK:
                index += 1
                continue
            if _ASSIGNMENT.match(word):
                index += 1
                continue
            if word in _STOP_KEYWORDS or word in _BUILTINS:
                index = _next_segment(words, index)
                continue
            if word in _SKIP_KEYWORDS:
                index += 1
                continue
            prefix = _PREFIXES.get(word.rsplit("/", 1)[-1])
            if prefix is not None:
                index = _skip_prefix(words, index + 1, prefix)
                continue
            if word.startswith("-") or _IMPLAUSIBLE_NAME.search(word) is not None:
                index = _next_segment(words, index)
                continue
            return word
        return None

    def _normalize(self, name: str) -> str:
        cfg = self._cfg
        if cfg.basename:
            name = name.rsplit("/", 1)[-1]
        name = self._version_suffix.sub("", name) or name
        return cfg.aliases.get(name, name)


_SEGMENT_BREAK: Final = "\x00"
"""Sentinel word marking a top-level operator between two simple commands.

A real word can never equal it: the scanner never emits a NUL, and bashlex's
word list is joined per command node with the same sentinel.
"""


# ---------------------------------------------------------------------------
# bashlex tree walk
# ---------------------------------------------------------------------------


def _collect_command_words(node: Any, out: list[str]) -> None:
    """Depth-first over structural nodes, stopping AT each simple command.

    Never descends into a command's own word parts, because a command
    substitution lives there: `echo $(date) && ls` must resolve to `ls`, not
    `date` — the substitution runs for the `echo`, which is a builtin.
    """
    kind = getattr(node, "kind", None)
    if kind == "command":
        if out:
            out.append(_SEGMENT_BREAK)
        for part in getattr(node, "parts", ()):
            if getattr(part, "kind", None) in {"word", "assignment"}:
                out.append(str(getattr(part, "word", "")))
        return
    if kind in {"reservedword", "word", "redirect", "operator", "pipe"}:
        return
    for child in (*getattr(node, "parts", ()), *getattr(node, "list", ())):
        _collect_command_words(child, out)


# ---------------------------------------------------------------------------
# The fallback scan
# ---------------------------------------------------------------------------


def _strip_heredocs(text: str) -> str:
    """Remove every heredoc operator and its BODY, keeping the rest verbatim.

    Bodies are prose — markdown tables, JSON, patches — and every `|`, `;` and
    `&&` in them reads as an operator to any scan that does not do this.
    """
    lines = text.split("\n")
    kept: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        delimiters = _heredoc_delimiters(line)
        kept.append(_HEREDOC_OPERATOR.sub(" ", line) if delimiters else line)
        for delimiter in delimiters:
            while index < len(lines):
                terminator = lines[index].strip() == delimiter
                index += 1
                if terminator:
                    break
    return "\n".join(kept)


def _heredoc_delimiters(line: str) -> list[str]:
    """The heredoc delimiters this line opens, in order, ignoring quoted `<<`."""
    delimiters: list[str] = []
    for match in _HEREDOC_OPERATOR.finditer(line):
        if _inside_quotes(line, match.start()):
            continue
        delimiters.append(match.group(2) if match.group(2) is not None else match.group(3))
    return delimiters


def _inside_quotes(line: str, position: int) -> bool:
    quote: str | None = None
    index = 0
    while index < position and index < len(line):
        char = line[index]
        if quote is None and char in "'\"":
            quote = char
        elif quote is not None and char == quote:
            quote = None
        elif quote != "'" and char == "\\":
            index += 1
        index += 1
    return quote is not None


def _scan(text: str) -> list[_Token]:  # noqa: PLR0912, PLR0915 - one state machine
    """Quote-aware tokenization into words, operators and redirections.

    Deliberately not `shlex`: it raises on the unbalanced quotes real commands
    contain, and it has no notion of an operator, so a caller has to re-find
    them in text it has already lost the quoting of.
    """
    tokens: list[_Token] = []
    buffer: list[str] = []
    length = len(text)
    index = 0

    def flush() -> None:
        if buffer:
            tokens.append(_Token("word", "".join(buffer)))
            buffer.clear()

    def flush_before_redirect() -> None:
        # `2>&1` — the leading file descriptor is part of the redirection, not
        # a word, and emitting it would offer `2` as an executable.
        if buffer and not "".join(buffer).isdigit():
            tokens.append(_Token("word", "".join(buffer)))
        buffer.clear()

    while index < length:
        char = text[index]
        if char == "'":
            end = text.find("'", index + 1)
            end = length if end < 0 else end
            buffer.append(text[index + 1 : end])
            index = end + 1
            continue
        if char == '"':
            index += 1
            while index < length and text[index] != '"':
                if text[index] == "\\" and index + 1 < length:
                    buffer.append(text[index + 1])
                    index += 2
                    continue
                buffer.append(text[index])
                index += 1
            index += 1
            continue
        if char == "\\":
            if index + 1 < length and text[index + 1] != "\n":
                buffer.append(text[index + 1])
            index += 2
            continue
        if char == "$" and index + 1 < length and text[index + 1] == "(":
            end = _skip_balanced(text, index + 1)
            buffer.append(text[index:end])
            index = end
            continue
        if char == "`":
            end = text.find("`", index + 1)
            end = length if end < 0 else end + 1
            buffer.append(text[index:end])
            index = end
            continue
        if char in " \t\r":
            flush()
            index += 1
            continue
        if char == "#" and not buffer:
            # A comment runs to end of line. Without this a commented heredoc
            # or a documented multi-line script offers `#` as an executable —
            # 50 real occurrences before the case was handled.
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        pair = text[index : index + 2]
        if pair in _TWO_CHAR_REDIRECTS:
            flush_before_redirect()
            tokens.append(_Token("redirect", pair))
            index += 2
            continue
        if pair in _TWO_CHAR_OPERATORS:
            flush()
            tokens.append(_Token("op", pair))
            index += 2
            continue
        if char in "<>":
            flush_before_redirect()
            tokens.append(_Token("redirect", char))
            index += 1
            continue
        if char in "|&;\n()":
            flush()
            tokens.append(_Token("op", char))
            index += 1
            continue
        buffer.append(char)
        index += 1
    flush()
    return tokens


def _skip_balanced(text: str, start: int) -> int:
    """The index just past the `)` closing the `(` at `start`."""
    depth = 0
    index = start
    while index < len(text):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


# ---------------------------------------------------------------------------
# Word-list walking, shared by both roads
# ---------------------------------------------------------------------------


def _next_segment(words: list[str], index: int) -> int:
    while index < len(words) and words[index] != _SEGMENT_BREAK:
        index += 1
    return index + 1


def _skip_prefix(words: list[str], index: int, prefix: _Prefix) -> int:
    """Walk past a prefix command's own flags, values and positionals."""
    while index < len(words):
        word = words[index]
        if word == _SEGMENT_BREAK:
            return index
        if word == "--":
            index += 1
            break
        if prefix.assignments and _ASSIGNMENT.match(word):
            index += 1
            continue
        if not word.startswith("-") or word == "-":
            break
        if "=" in word:  # --flag=value
            index += 1
            continue
        if word in prefix.value_flags:
            index += 2
            continue
        if len(word) > 2 and not word.startswith("--") and word[:2] in prefix.value_flags:
            index += 1  # attached short value: -I{}, -n1
            continue
        index += 1
    for _ in range(prefix.positionals):
        if index < len(words) and words[index] != _SEGMENT_BREAK:
            index += 1
    return index
