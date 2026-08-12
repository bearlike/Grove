"""The whole demo world, loaded and validated from `demo.json`.

`DemoWorld` is the ONE place a screenshot's content is declared. Changing what a
capture shows is an edit to that file, not to code — which is the point of the
split: `planter/` writes whatever this says onto disk, and `driver/` frames
whatever the planter produced without ever reading either.

The models here ARE the schema. There is no second schema file, and
``extra="forbid"`` everywhere means a typo in `demo.json` fails the load rather
than silently dropping a workspace from a screenshot.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .history import HistoryCorpus
from .transcript import ToolStep
from .weights import weighted
from .workspace import DemoWorkspace

_FROZEN = ConfigDict(extra="forbid", frozen=True)

DEMO_PATH: Final[Path] = Path(__file__).with_name("demo.json")

AdapterKind = Literal["claude_code", "codex"]
"""The two agent kinds with a transcript writer. Narrow because it selects one."""


class ModelPrices(BaseModel):
    """Public list prices per MILLION tokens, per class.

    Every Codex entry prices ``input`` at 0.0 DELIBERATELY, and it is a finding
    rather than a rounding: `_session_row` (`core/usage/projector.py`) nulls a
    Codex session's `fresh_input` whenever the rollout reports a cumulative
    total — the provider total is authoritative there — while `PriceBook.amount`
    refuses to price a class that has a non-zero rate and no count, and a range
    cost is all-or-unknown. So ONE non-zero Codex input rate makes every Codex
    session unpriceable and takes the entire Cost tile down with it. The cost of
    saying it this way is stated rather than hidden: the estimate charges Codex
    for output and cache reads only, so the Codex share of it reads LOW.
    """

    model_config = _FROZEN

    input: float = Field(ge=0.0)
    output: float = Field(ge=0.0)
    cache_read: float = Field(ge=0.0)
    cache_write: float = Field(ge=0.0)


class Pricing(BaseModel):
    """The price table behind the usage page's Cost tile.

    The figure is an ESTIMATE and the product says so on the wire — a
    subscription session never paid these.
    """

    model_config = _FROZEN

    currency: str = "USD"
    models: dict[str, ModelPrices] = Field(min_length=1)

    def as_config(self) -> dict[str, Any]:
        """The shape `usage.pricing` takes in a Grove config layer."""
        return {
            "currency": self.currency,
            "models": {name: price.model_dump() for name, price in self.models.items()},
        }


class Repos(BaseModel):
    """The two live-fleet repos, by role rather than by name.

    ``primary`` is the repo the TUI opens on; ``secondary`` exists so the
    project switcher and the dashboard have a second one to show.
    """

    model_config = _FROZEN

    primary: str = Field(min_length=1)
    secondary: str = Field(min_length=1)

    @property
    def names(self) -> tuple[str, str]:
        return (self.primary, self.secondary)


class Tempo(BaseModel):
    """How fast a planted transcript's clock runs, record by record.

    These gaps ARE the durations the Activity card reports: ``generation_ms`` is
    the gap from a user/tool record to the assistant record answering it, and
    ``tool_ms`` is the gap from a tool call to its result (`_derived_intervals`
    in `core/usage/projector.py`). A writer that stamped every line one minute
    apart — as this one once did — plants a fleet where every tool call took
    exactly 60 s.
    """

    model_config = _FROZEN

    think_ms: tuple[int, int]
    tool_ms: tuple[int, int]
    fast_tool_ms: tuple[int, int]
    """A read or an edit: there is no process to wait on."""
    slow_tool_ms: tuple[int, int]
    """A test suite, an install, a build — the calls that dominate the mean."""
    human_ms: tuple[int, int]
    """Reading the answer and typing the next prompt — deliberately excluded
    from active time by the projector, which is why it may be this large."""

    slow_odds: dict[str, float]
    """How often a shell call led by this executable is one of the slow ones.

    The bash-command ranking ranks by TIME, so a corpus where every executable
    averages the same duration ranks them by call count wearing a clock's
    clothes. A `pytest` run and a `git status` differ by two orders of
    magnitude, and that difference is the only thing the card exists to show.
    """
    slow_odds_default: float
    """Anything not named above — an unfamiliar command is usually quick."""

    context_caps: tuple[tuple[int, float], ...] = Field(min_length=1)
    """Context windows the planted models run in, and how often each is drawn."""

    def tool_span(self, step: ToolStep, rng: random.Random) -> tuple[int, int]:
        """How long this one call takes, from what it is actually doing."""
        if step.command is None:
            return self.fast_tool_ms
        odds = self.slow_odds.get(step.leading_executable, self.slow_odds_default)
        return self.slow_tool_ms if rng.random() < odds else self.tool_ms

    def context_cap(self, rng: random.Random) -> int:
        return weighted(rng, self.context_caps)


class DemoWorld(BaseModel):
    """Everything a screenshot shows, in one validated object."""

    model_config = _FROZEN

    base_time: datetime
    """Every LIVE-fleet transcript anchors here rather than to "now", so the
    captures stay byte-stable across runs on different days. The historical
    backfill uses its own spread of real, backdated timestamps instead — it
    exists precisely to vary across days."""

    models: dict[AdapterKind, str]
    """The model each live session reports. Published product names, never
    host-private facts."""

    pricing: Pricing
    repos: Repos
    tempo: Tempo

    question: dict[str, Any]
    """A structured question the agent asks the user, planted as an
    ``AskUserQuestion`` tool call so the adapter renders it as a question card
    with selectable choices; the turn's tool ``result`` is the chosen answer."""

    workspaces: tuple[DemoWorkspace, ...] = Field(min_length=1)
    """In CREATION order. `WorkspaceManager.list()` returns newest-first, so the
    last entry floats to the top and becomes the default selection."""

    history: HistoryCorpus

    @classmethod
    def load(cls, path: Path = DEMO_PATH) -> DemoWorld:
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def workspace(self, title: str) -> DemoWorkspace:
        """The one workspace with this title, or a loud `KeyError`."""
        for entry in self.workspaces:
            if entry.title == title:
                return entry
        raise KeyError(title)

    def in_repo(self, repo: str) -> tuple[DemoWorkspace, ...]:
        return tuple(entry for entry in self.workspaces if entry.repo == repo)
