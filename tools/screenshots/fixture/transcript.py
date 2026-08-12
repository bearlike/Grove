"""What one planted agent session SAID — turns, tool calls, and nothing else.

Pure declarative shapes. A `Transcript` knows how many turns it has and whether
it ends mid-tool-call; it knows nothing about JSONL, rollouts, or where either
lands on disk. The two writers that project these onto a provider's wire format
live in `tools/screenshots/planter/`.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

_FROZEN = ConfigDict(extra="forbid", frozen=True)


class ToolStep(BaseModel):
    """One tool invocation inside a planted turn.

    ``input`` mirrors what each harness's own wire shape expects: a mapping of
    named arguments for every ordinary tool, or a raw patch-body STRING for
    Codex's ``apply_patch`` custom tool call — Codex records the patch body
    verbatim, never as JSON (see ``src/grove/core/agents/codex.py``).

    ``result is None`` leaves the call OPEN: no matching tool result is ever
    written, so the session's tail reads WORKING. Only the final step of a
    turn's tool list may do this, and only on that session's LAST turn —
    `Transcript.ends_open` is what asserts it.

    ``name`` is deliberately NOT a `Literal`. The branching sets below are
    closed and the predicates below narrow them, but the tool vocabulary itself
    is open: a transcript may legitimately name any MCP server's tool, and a
    closed type there would make adding one an edit to this module.
    """

    model_config = _FROZEN

    PATCH_TOOL: ClassVar[str] = "apply_patch"
    """Codex's custom tool call — the one whose ``input`` is a raw string."""

    SHELL_TOOLS: ClassVar[frozenset[str]] = frozenset({"Bash", "exec_command"})
    """The tools whose duration is a process's, so `Tempo` prices them by their
    leading executable rather than as a read."""

    name: str = Field(min_length=1)
    input: dict[str, Any] | str
    result: str | None = None
    is_error: bool = False

    @property
    def is_patch(self) -> bool:
        return self.name == self.PATCH_TOOL

    @property
    def is_open(self) -> bool:
        return self.result is None

    @property
    def command(self) -> str | None:
        """The command line a shell step runs, or ``None`` for any other tool.

        Read off the input rather than off `SHELL_TOOLS`, because the two
        harnesses name the argument differently (``command`` / ``cmd``) and an
        MCP tool that happens to carry one is still a command being run.
        """
        if not isinstance(self.input, dict):
            return None
        raw = self.input.get("command") or self.input.get("cmd")
        return raw if isinstance(raw, str) else None

    @property
    def leading_executable(self) -> str:
        """The first word of `command`, or ``""`` — what slow-odds key off."""
        command = self.command
        if command is None:
            return ""
        parts = command.split()
        return parts[0] if parts else ""


class Turn(BaseModel):
    """One planted human/assistant exchange.

    ``thinking``, when set, is rendered as its own ``thinking`` content block
    ahead of any tool calls — one of the four block kinds
    ``claude_code.py::_map_blocks`` parses (the others are ``text``,
    ``tool_use``, ``tool_result``); Codex's equivalent is a ``reasoning``
    record with a readable ``summary``.
    """

    model_config = _FROZEN

    prompt: str
    reply: str = ""
    thinking: str | None = None
    tools: tuple[ToolStep, ...] = ()

    @property
    def open_step(self) -> ToolStep | None:
        """The first unresolved step, after which this turn stops."""
        return next((step for step in self.tools if step.is_open), None)

    def resolved_steps(self) -> tuple[ToolStep, ...]:
        """Every step up to (not including) the one that leaves the turn open."""
        resolved: list[ToolStep] = []
        for step in self.tools:
            if step.is_open:
                break
            resolved.append(step)
        return tuple(resolved)


class Transcript(BaseModel):
    """One agent session's whole recorded conversation.

    ``ai_title`` is Claude Code's own summary line and has no Codex equivalent,
    so a Codex transcript simply leaves it unset.
    """

    model_config = _FROZEN

    ai_title: str | None = None
    turns: tuple[Turn, ...] = Field(min_length=1)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def ends_open(self) -> bool:
        """True when this session's tail reads WORKING rather than idle."""
        return self.turns[-1].open_step is not None
