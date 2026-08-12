"""Backdate a year of synthetic sessions across the sandbox profile roots.

Transcript files ONLY — planted straight into the sandbox
``CLAUDE_CONFIG_DIR``/``CODEX_HOME`` the caller already pointed at a throwaway
tree, through the same two writers the live fleet uses, never through
``WorkspaceManager``. Nothing here mints a worktree, a tmux session, or a
workspace record.

The usage projector discovers these the same way it discovers any transcript on
the host (``grove.core.usage.projector.UsageProjector._references``): it scans
the ambient ``CLAUDE_CONFIG_DIR``/``CODEX_HOME`` unconditionally, with no
workspace or "known root" gate — a real transcript file is enough.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.screenshots.fixture import (
    HistoryCorpus,
    HistoryDay,
    HistoryProvider,
    Tempo,
    ToolStep,
    Transcript,
    Turn,
)

from .claude import ClaudeTranscriptPlanter
from .codex import CodexRolloutPlanter
from .repo import GitTree
from .timeline import rng_uuid


class HistoryPlanter:
    """Draws the corpus and hands each session to the writer for its provider.

    ``corpus.seed`` pins every choice INCLUDING the session ids, so a re-run
    reproduces the identical history; each session's own timestamps, token
    counts and inner record ids then derive from its id. Only the anchor moves —
    the history is laid out relative to the day it is planted, so it always ends
    today.
    """

    def __init__(
        self,
        *,
        corpus: HistoryCorpus,
        tempo: Tempo,
        claude: ClaudeTranscriptPlanter,
        codex: CodexRolloutPlanter,
    ) -> None:
        self._corpus = corpus
        self._tempo = tempo
        self._claude = claude
        self._codex = codex

    def plant(self, work: Path) -> None:
        """Write the whole corpus, oldest day first.

        The order is load-bearing: `CodexQuotaProvider` reads the five newest
        rollout files BY MTIME, and written newest-day-first the last file on
        disk is the oldest session in the corpus — so the subscription card
        would report a window that reset a year ago.
        """
        rng = random.Random(self._corpus.seed)
        repos = [GitTree.init_repo(work, name).path for name in self._corpus.repos]
        now = datetime.now(tz=UTC)
        for day in reversed(self._corpus.curve.calendar(rng, today=now)):
            for _ in range(day.sessions):
                self._session(rng, day=day, repos=repos, now=now)

    def _session(
        self, rng: random.Random, *, day: HistoryDay, repos: list[Path], now: datetime
    ) -> None:
        provider = self._corpus.provider(rng)
        model = self._corpus.model(rng, provider)
        base = self._start(rng, day=day, now=now)
        files = tuple(rng.sample(self._corpus.files, k=rng.randint(*self._corpus.working_set)))
        turns = tuple(self._turn(rng, provider, files) for _ in range(self._corpus.turn_count(rng)))
        transcript = Transcript(
            ai_title=None if provider == "codex" else turns[0].prompt, turns=turns
        )
        session_id = rng_uuid(rng)
        cwd = rng.choice(repos)
        if provider == "codex":
            self._codex.plant(
                cwd=cwd,
                session_id=session_id,
                branch="current",
                transcript=transcript,
                model=model,
                base=base,
            )
        else:
            self._claude.plant(
                cwd=cwd,
                session_id=session_id,
                transcript=transcript,
                model=model,
                base=base,
            )

    def _start(self, rng: random.Random, *, day: HistoryDay, now: datetime) -> datetime:
        """When this session began — never later than the hour it is now.

        Today is only partly over, so a session starting at 21:00 on it would
        sit in the future and read as a session that has not happened.
        """
        earliest, latest = self._corpus.session_hours
        if day.at.date() == now.date():
            latest = max(earliest, now.hour - 1)
        return day.at.replace(
            hour=rng.randint(earliest, latest),
            minute=rng.randint(0, 59),
            second=rng.randint(0, 59),
            microsecond=0,
        )

    def _turn(self, rng: random.Random, provider: HistoryProvider, files: tuple[str, ...]) -> Turn:
        """One turn: a prose exchange with a burst of tool calls under it.

        Tool calls per turn are drawn log-normally rather than uniformly,
        because the distribution is what makes the sessions table interesting —
        most turns are a handful of calls and a few are a long grind.
        """
        prompt = rng.choice(self._corpus.prompts)
        reply = rng.choice(self._corpus.replies)
        return Turn(
            prompt=prompt,
            reply=reply,
            tools=tuple(
                self._tool_step(rng, provider, files) for _ in range(self._corpus.tool_count(rng))
            ),
        )

    def _tool_step(
        self, rng: random.Random, provider: HistoryProvider, files: tuple[str, ...]
    ) -> ToolStep:
        """One synthetic tool call, drawn from the corpus's tool mix.

        ``files`` is the session's own working set, so `files_changed` is the
        handful of paths one task touches rather than the whole pool. Failure
        rate is ~2% overall and slightly higher for a shell call (~3%), per the
        real-host proportions this backfill is modeled on.
        """
        tool = self._corpus.tool(rng)
        if tool == "mcp":
            # An MCP tool is called by the same name whichever harness calls it.
            return ToolStep(
                name=rng.choice(self._corpus.mcp_tools),
                input={"query": rng.choice(self._corpus.prompts)},
                result="ok",
            )
        name = self._corpus.codex_name(tool) if provider == "codex" else tool
        is_shell = name in ToolStep.SHELL_TOOLS
        odds = self._corpus.shell_failure_odds if is_shell else self._corpus.failure_odds
        failed = rng.random() < odds

        tool_input: dict[str, Any] | str
        if is_shell:
            command = rng.choice(self._corpus.commands)
            tool_input = (
                {"cmd": command}
                if provider == "codex"
                else {"command": command, "description": "run the command"}
            )
            result = f"$ {command}\n" + ("command exited 1\n" if failed else "ok\n")
        elif name == ToolStep.PATCH_TOOL:
            path = rng.choice(files)
            tool_input = (
                f"*** Begin Patch\n*** Update File: {path}\n@@\n-old\n+new\n*** End Patch\n"
            )
            result = "error: patch does not apply" if failed else f"Applied patch to {path}"
        elif tool in self._corpus.edit_tools:
            path = rng.choice(files)
            tool_input = {"file_path": path, "old_string": "old", "new_string": "new"}
            result = "Error: string not found" if failed else f"The file {path} has been updated."
        elif tool in ("Grep", "Glob"):
            tool_input = {
                "pattern": rng.choice(("def resolve", "TODO", "**/*.tsx", "class .*Error"))
            }
            result = "No matches found" if failed else "\n".join(rng.sample(files, k=3))
        else:
            path = rng.choice(files)
            tool_input = {"file_path": path}
            result = "Error: not found" if failed else f"read {path}"
        return ToolStep(name=name, input=tool_input, result=result, is_error=failed)
