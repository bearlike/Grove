"""Which tool call IS a shell command, and what did it ask the shell to run.

One module answers one question, and two very different consumers ask it: the
usage audit (which bills a call's duration to its leading executable) and the
telemetry export (which publishes a canonical shell observation an evaluator can
select). Before this module each kept its own copy of the tool-name set and of
"where in the arguments the command lives", which is how a harness added to one
silently stayed invisible to the other.

**Classification is by exact provider tool NAME, never by substring and never by
inspecting the payload.** A tool called ``BashOutput`` is not a shell call, a
tool whose arguments happen to carry a ``command`` key is not one either, and
converting an arbitrary tool into a shell observation would be Grove inventing a
semantic the harness never stated. The set below is the census of shapes real
transcripts and real OTLP exports on this host actually carry; adding an alias
needs the same kind of evidence.

**Argv is preserved, never quietly flattened.** A harness that hands over
``["bash", "-lc", "echo hi"]`` said something more precise than any single line
can: word boundaries are already decided, so nothing downstream has to guess
where they were. :attr:`ShellCall.argv` keeps that list, and
:attr:`ShellCall.command` renders it with :func:`shlex.join` — a quoting-correct
rendering, unlike a bare space join, which turns ``["sh", "-c", "a b"]`` into a
line that means something else.
"""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

SHELL_TOOL_NAMES: Final = frozenset(
    {
        # Claude Code's transcript and its native OTLP both spell the tool
        # `Bash`. `BashOutput`/`KillShell` are deliberately absent: they address
        # an already-running background job by id and carry no command of their
        # own, so counting them as shell calls would only inflate the
        # unattributed bucket and publish an observation with nothing to grade.
        "Bash",
        # Codex. `exec_command` is the current shape (10,872+ real calls);
        # `shell`/`local_shell` are the older function names, kept because a
        # historical rollout on disk still carries them. `write_stdin` is
        # absent for the same reason `BashOutput` is — it feeds a session that
        # some earlier call created, and is not itself a command.
        "exec_command",
        "shell",
        "local_shell",
    }
)
"""Tool names whose call IS a shell command.

Shared by the usage projector (which fills `target` and the ranking query) and
by the telemetry export (which emits the canonical shell observation), so the
two cannot disagree about what counts as a shell call.
"""

_COMMAND_KEYS: Final = ("command", "cmd", "full_command", "bash_command")
"""Where each harness puts the command, in preference order.

`command` is Claude's `Bash` tool input (a string) and `cmd` is Codex's
`exec_command` (a string, or the `["bash", "-lc", …]` argv of the OpenAI
local-shell shape). The last two are Claude Code's NATIVE OTLP spelling, both
present in the checked-in capture under `tests/core/data/otlp_claude_code/`:
`full_command` rides the `claude_code.tool` span itself and `bash_command`
rides its `tool.output` event, carrying the same value there — so the span's
own key is preferred and the event's is the fallback for a span that carried
none.
"""

_BACKGROUND_KEY: Final = "run_in_background"
"""Claude's `Bash` flag asking the harness to detach the call.

Absent from every other shape here, which is why :attr:`ShellCall.background`
is nullable: a harness that has no such concept must not be reported as having
declined to use it.
"""


@dataclass(frozen=True, slots=True)
class ShellCall:
    """What one shell tool call asked the shell to run, provider-neutral.

    A value object rather than three loose lookups because the three facts are
    read together at every call site and a consumer that reads the command
    without the argv beside it has silently lost the more precise half.
    """

    command: str
    """The command line, as text.

    The provider's own string when it gave one, otherwise :attr:`argv` rendered
    with `shlex.join`. Never empty — :meth:`of` returns ``None`` instead.
    """

    argv: tuple[str, ...] | None = None
    """The provider's own argument vector, when it handed one over.

    ``None`` means the provider gave a command LINE and the words in it were
    never separated by anything but a shell — not that the line has no words.
    """

    background: bool | None = None
    """Whether the harness was asked to detach this call.

    ``None`` when the provider's schema carries no such flag, which is a
    different fact from ``False``: a Codex `exec_command` did not decline to run
    in the background, it had no way to ask.
    """

    @classmethod
    def of(cls, tool_name: str | None, tool_input: Mapping[str, Any] | None) -> ShellCall | None:
        """The shell call this tool invocation is, or ``None`` if it is not one.

        ``None`` covers three genuinely different non-answers on purpose — a
        tool that is not a shell tool, a shell tool whose arguments Grove could
        not read, and a shell tool that carried no command at all. All three
        mean *do not publish a shell observation for this*, and separating them
        would invite a caller to publish one for two of them.
        """
        if tool_name not in SHELL_TOOL_NAMES or not isinstance(tool_input, Mapping):
            return None
        for key in _COMMAND_KEYS:
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return cls(command=value, argv=None, background=cls._background(tool_input))
            if isinstance(value, list):
                argv = tuple(part for part in value if isinstance(part, str))
                if argv and shlex.join(argv).strip():
                    return cls(
                        command=shlex.join(argv),
                        argv=argv,
                        background=cls._background(tool_input),
                    )
        return None

    @staticmethod
    def _background(tool_input: Mapping[str, Any]) -> bool | None:
        """The detach flag if this shape carried one, else ``None``.

        Keyed on the flag's PRESENCE and then read for truthiness, which is the
        one reading that both preserves what the usage audit has always counted
        and still separates *this harness cannot ask* from *this call did not*.
        """
        if _BACKGROUND_KEY not in tool_input:
            return None
        return bool(tool_input.get(_BACKGROUND_KEY))


__all__ = ["SHELL_TOOL_NAMES", "ShellCall"]
