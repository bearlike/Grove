"""CommandParser — the pure issue-comment grammar.

Turns a raw comment body into a :class:`ParsedCommand`, or ``None`` when the
comment doesn't open with the trigger token. No I/O, no config object — the
trigger is passed in (it is config-cascaded data the engine resolves per repo),
so the parser stays a pure, table-testable unit.

The grammar, deliberately tiny (the industry-converged inbound default):

* the ``trigger`` must be the FIRST token, word-boundary matched — a longer
  handle (``@grovebot``) or a mid-comment mention never fires;
* a fixed verb set (:data:`VERBS`) as the sole word after the trigger is a
  ``verb`` command; that same verb with any trailing junk is a ``usage`` reply
  (a lone verb takes no arguments);
* anything else after the trigger is free-text ``prompt`` — a non-verb first
  word is just the start of a prompt, never a mistyped verb to second-guess;
* a bare trigger with nothing after it is ``usage`` (never silence).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IssueCommandKind = Literal["verb", "prompt", "usage"]
"""The three shapes a triggered comment resolves to. ``None`` (not a
:class:`ParsedCommand`) is the fourth, off-band outcome: not triggered at all."""

VerbName = Literal["status", "pause", "resume", "stop"]
"""The fixed verb set. Kept a closed literal so the engine's routing switch is
exhaustive and a typo is a type error, not a silent free-text fall-through."""

VERBS: frozenset[str] = frozenset({"status", "pause", "resume", "stop"})
"""Runtime membership test for verb detection (the literal above drives typing)."""

# Leading punctuation stripped off the post-trigger remainder so ``@grove: fix``
# and ``@grove, status`` read as ``fix`` / ``status`` — the mention is often
# written with a comma or colon before the command.
_LEADING_JUNK = " \t:,"


@dataclass(frozen=True, slots=True)
class ParsedCommand:
    """The parse result for a triggered comment.

    ``verb`` is set for ``kind`` in ``{"verb", "usage"}`` (usage names the
    offending verb when one was recognized); ``text`` carries the prompt for
    ``kind == "prompt"``; ``reason`` is a human explanation the engine folds into
    a usage reply. Defaults keep the unused fields empty so equality in tests is
    field-exact.
    """

    kind: IssueCommandKind
    verb: str | None = None
    text: str = ""
    reason: str = ""


class CommandParser:
    """Pure trigger-and-verb grammar over a comment body. Stateless; reusable."""

    def parse(self, body: str, *, trigger: str) -> ParsedCommand | None:
        """Parse ``body`` against ``trigger``; ``None`` when it isn't triggered.

        The trigger must open the comment at a word boundary (``(?!\\w)`` after it,
        so ``@grovebot`` is not ``@grove``), matched case-insensitively like a
        forge mention. Everything after it — minus leading ``:``/``,`` punctuation
        — is the command.
        """
        stripped = body.strip()
        match = re.match(rf"{re.escape(trigger)}(?!\w)", stripped, flags=re.IGNORECASE)
        if match is None:
            return None
        rest = stripped[match.end() :].lstrip(_LEADING_JUNK).strip()
        if not rest:
            return ParsedCommand(kind="usage", reason=f"{trigger} needs a command or a prompt")
        tokens = rest.split()
        head = tokens[0].lower()
        if head in VERBS:
            if len(tokens) > 1:
                return ParsedCommand(
                    kind="usage", verb=head, reason=f"the {head!r} command takes no arguments"
                )
            return ParsedCommand(kind="verb", verb=head)
        return ParsedCommand(kind="prompt", text=rest)


__all__ = ["VERBS", "CommandParser", "IssueCommandKind", "ParsedCommand", "VerbName"]
