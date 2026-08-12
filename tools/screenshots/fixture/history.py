"""The year of backdated, transcript-only sessions behind the usage page.

The usage page is derived wholly from projecting transcripts
(`grove.core.usage.projector`), so with only the eight live-fleet sessions its
heatmap, breakdowns, sessions table and bash-command ranking all render nearly
empty. This declares the corpus that fills them: how much work happened on each
of 365 days, and the vocabulary each synthetic session draws from.

Proportions are ROUGH — the claude/codex split, turns per session, tool calls
per turn, the tool mix, the share that fail — modeled loosely on one real heavy
instance and scaled up, never claimed as measured. What the corpus actually
produced is read back through `UsageService`, never asserted here.

Nothing in this module writes a file. `planter/history.py` is what turns a
`HistoryDay` into transcripts on disk.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .weights import weighted

_FROZEN = ConfigDict(extra="forbid", frozen=True)

HistoryProvider = Literal["claude_code", "codex"]
"""The two adapter kinds the backfill plants — the branch that decides which
writer runs and which model table is drawn from."""

HistoryToolName = Literal[
    "Bash",
    "Read",
    "Edit",
    "Grep",
    "SendMessage",
    "Glob",
    "ToolSearch",
    "Write",
    "TodoWrite",
    "MultiEdit",
    "WebFetch",
    "AskUserQuestion",
    "mcp",
]
"""The tool-mix vocabulary, keyed by Claude Code's own names.

Closed, unlike `ToolStep.name`: every member drives a branch (which Codex tool
it maps onto, whether a synthetic call is a shell call, an edit or a read), and
``mcp`` is the sentinel that resolves to a name from `HistoryCorpus.mcp_tools`
instead of spelling forty more rows into the weight table.
"""


class HistoryDay(BaseModel):
    """One calendar day of the backfill: when, and how much work happened."""

    model_config = _FROZEN

    at: datetime
    sessions: int = Field(ge=0)

    @property
    def lit(self) -> bool:
        return self.sessions > 0


class HistoryCurve(BaseModel):
    """The year's daily intensity — the shape the heatmap is actually drawn from.

    ``sessions`` is the one knob that moves the whole corpus together: every
    downstream total (turns, tool calls, tokens, cost) is this number times a
    per-session distribution, and so is the seeding cost.
    """

    model_config = _FROZEN

    sessions: int = Field(gt=0)
    days: int = Field(gt=0)
    dark_runs: int = 3
    """Whole stretches off — holidays, time off, a conference week."""
    dark_run_days: tuple[int, int] = (4, 8)
    weekend_weight: float = 0.35
    weekend_dark_odds: float = 0.35
    stray_dark_odds: float = 0.02
    seasonal_period: float = 58.0
    jitter: tuple[float, float] = (0.45, 1.6)
    surge_odds: float = 0.07
    surge_factor: float = 2.4

    def calendar(self, rng: random.Random, *, today: datetime) -> tuple[HistoryDay, ...]:
        """Every day of the window, newest first, with its session count.

        A flat wall of identical cells argues nothing, so a day's weight is the
        product of three independent things: a weekday rhythm (a weekend is
        about a third of a weekday), a slow seasonal wave, and per-day jitter,
        with a few whole weeks off and the occasional day that goes twice as
        hard.

        Dark days are chosen EXPLICITLY rather than falling out of rounding, and
        a lit day is floored at one session, so "how much of the year is lit" is
        a property of this method rather than an accident of the scale factor.
        """
        dark = self._dark_days(rng)
        weights = [self._weight(rng, offset, today=today, dark=dark) for offset in range(self.days)]
        scale = self._scale(weights)
        return tuple(
            HistoryDay(
                at=today - timedelta(days=offset),
                sessions=max(1, round(weight * scale)) if weight else 0,
            )
            for offset, weight in enumerate(weights)
        )

    def _dark_days(self, rng: random.Random) -> set[int]:
        dark: set[int] = set()
        for _ in range(self.dark_runs):
            start = rng.randrange(self.days - self.dark_run_days[1])
            dark.update(range(start, start + rng.randint(*self.dark_run_days)))
        return dark

    def _weight(self, rng: random.Random, offset: int, *, today: datetime, dark: set[int]) -> float:
        weekend = (today - timedelta(days=offset)).weekday() >= 5
        if (
            offset in dark
            or (weekend and rng.random() < self.weekend_dark_odds)
            or rng.random() < self.stray_dark_odds
        ):
            return 0.0
        weight = (self.weekend_weight if weekend else 1.0) * (
            0.75 + 0.45 * math.sin(offset / self.seasonal_period)
        )
        weight *= rng.uniform(*self.jitter)
        if rng.random() < self.surge_odds:
            weight *= self.surge_factor
        return weight

    def _scale(self, weights: list[float]) -> float:
        """Solve for the factor that lands the floored sum on `sessions`.

        Flooring every lit day at one session overshoots any factor derived by
        division — by however many quiet days round up — so the factor is SOLVED
        for instead. Bisection, because the floor makes the relation monotonic
        but not invertible.
        """
        lit = [weight for weight in weights if weight]
        if not lit:
            return 0.0
        low, high = 0.0, 4 * self.sessions / sum(lit)
        for _ in range(48):
            scale = (low + high) / 2
            if sum(max(1, round(weight * scale)) for weight in lit) > self.sessions:
                high = scale
            else:
                low = scale
        return low


class HistoryCorpus(BaseModel):
    """The vocabulary one synthetic session draws from, plus the mix it draws by.

    ``seed`` pins every choice INCLUDING the session ids, so a re-run reproduces
    the identical history byte for byte; each session's own timestamps, token
    counts and inner record ids then derive from its id. Only the anchor moves —
    the history is laid out relative to the day it is planted.
    """

    model_config = _FROZEN

    seed: int
    curve: HistoryCurve
    codex_share: float = Field(ge=0.0, le=1.0)
    """Codex's share of sessions. Two profile roots means exactly two accounts,
    so this is also the split the account breakdown shows."""

    repos: tuple[str, ...] = Field(min_length=1)
    """Fictional repos the sessions run in. With the live fleet's two, this is
    what gives the project breakdown a ranking worth drawing."""

    models: dict[HistoryProvider, tuple[tuple[str, float], ...]]
    tool_weights: tuple[tuple[HistoryToolName, float], ...] = Field(min_length=1)
    mcp_tools: tuple[str, ...] = Field(min_length=1)
    codex_tool_names: dict[HistoryToolName, str]
    """Codex has no Read/Grep/Glob/SendMessage/ToolSearch/WebFetch tools of its
    own — it shells out for all of those — so any weight-table name absent here
    falls back to ``exec_command``."""

    edit_tools: frozenset[str]
    """The tools the projector counts as touching a file — `files_changed` is
    the number of DISTINCT paths these named, which is why a session draws its
    own small working set rather than the whole pool."""

    commands: tuple[str, ...] = Field(min_length=1)
    files: tuple[str, ...] = Field(min_length=1)
    prompts: tuple[str, ...] = Field(min_length=1)
    replies: tuple[str, ...] = Field(min_length=1)

    turns_per_session: tuple[float, float] = (3.12, 0.72)
    """Log-normal (mu, sigma). Most sessions are a handful of turns and a few
    are a long grind, which is what makes the sessions table interesting."""
    max_turns: int = 140
    tools_per_turn: tuple[float, float] = (2.45, 0.8)
    max_tools: int = 60
    working_set: tuple[int, int] = (8, 22)
    shell_failure_odds: float = 0.03
    failure_odds: float = 0.02
    session_hours: tuple[int, int] = (7, 21)

    def provider(self, rng: random.Random) -> HistoryProvider:
        return "codex" if rng.random() < self.codex_share else "claude_code"

    def model(self, rng: random.Random, provider: HistoryProvider) -> str:
        return weighted(rng, self.models[provider])

    def tool(self, rng: random.Random) -> HistoryToolName:
        return weighted(rng, self.tool_weights)

    def codex_name(self, tool: HistoryToolName) -> str:
        return self.codex_tool_names.get(tool, "exec_command")

    def turn_count(self, rng: random.Random) -> int:
        return min(self.max_turns, max(2, int(rng.lognormvariate(*self.turns_per_session))))

    def tool_count(self, rng: random.Random) -> int:
        return min(self.max_tools, max(1, int(rng.lognormvariate(*self.tools_per_turn))))
