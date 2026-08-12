"""Everything ONE planted session's numbers derive from.

A transcript is a pure function of its session id: the id seeds one RNG, and
that RNG drives the clock between records, the prompt cache that grows across
requests, and the rate-limit envelope Codex writes onto every token report. The
backfill mints its ids from its own seeded RNG and therefore reproduces byte for
byte, while the live fleet's ids come from `WorkspaceManager` and vary per run.

Nothing here writes a file — the two writers in this package own that. What it
owns is the ORDER in which the RNG is consumed, which is why the clock, the
context window and the limits share one object instead of being three
independently seeded things a writer has to remember to advance together.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from tools.screenshots.fixture import Tempo, ToolStep


def rng_uuid(rng: random.Random) -> str:
    """A UUID4-shaped id drawn from a seeded RNG rather than from urandom.

    Free rather than a method because the backfill needs it BEFORE it has a
    session id to build a `SessionTimeline` with — minting the id is precisely
    what it is for.
    """
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


@dataclass(slots=True)
class SessionClock:
    """The wall clock of one planted transcript, advanced record by record."""

    at: datetime
    rng: random.Random
    tempo: Tempo

    def tick(self, span: tuple[int, int]) -> str:
        self.at += timedelta(milliseconds=self.rng.randint(*span))
        return self.at.strftime("%Y-%m-%dT%H:%M:%S.") + f"{self.at.microsecond // 1000:03d}Z"

    def think(self) -> str:
        return self.tick(self.tempo.think_ms)

    def human(self, *, first: bool) -> str:
        """The gap before a user prompt: a think on turn one, a read after."""
        return self.tick(self.tempo.think_ms if first else self.tempo.human_ms)

    def after(self, step: ToolStep) -> str:
        """The moment ``step``'s result lands, priced by what it is doing."""
        return self.tick(self.tempo.tool_span(step, self.rng))


@dataclass(slots=True)
class ContextWindow:
    """The prompt cache a session accumulates, one model request at a time.

    Every assistant message gets all FOUR token classes, always. The usage
    projection counts a session's tokens as MEASURED only when `fresh_input`,
    `cache_read`, `cache_creation` and `output` are all present
    (`_measured_total` in `core/usage/query.py`), so omitting one class alone
    left every planted session unmeasured and the daily token heatmap almost
    blank — while the session and tool-call heatmaps over the same corpus lit
    every day, which is what made it read as a rendering fault.

    The proportions are the ones a real session has, and they are what makes the
    token-class breakdown worth drawing: `cache_read` is the whole conversation
    replayed on every request, so it dominates by an order of magnitude and
    GROWS until the harness compacts; `cache_creation` is only the new slice
    written into the cache; `output` is the smallest number on the row.
    """

    rng: random.Random
    cap: int
    size: int = 12_000

    def request(self) -> dict[str, int]:
        written = self.rng.randint(900, 14_000)
        self.size = min(self.cap, self.size + written)
        usage = {
            "input_tokens": self.rng.randint(160, 3_200),
            "cache_read_input_tokens": self.size,
            "cache_creation_input_tokens": written,
            "output_tokens": self.rng.randint(90, 2_600),
        }
        if self.size >= self.cap:
            # Compaction: the harness summarizes and the replayed prompt
            # collapses back to that summary plus the last few turns.
            self.size = int(self.cap * self.rng.uniform(0.15, 0.30))
        return usage

    def turn_usage(self, *, requests: int) -> dict[str, int]:
        """One Codex turn's token report, summed over the requests it made."""
        usage = [self.request() for _ in range(requests)]
        fresh = sum(u["input_tokens"] for u in usage)
        cached = sum(u["cache_read_input_tokens"] for u in usage)
        output = sum(u["output_tokens"] for u in usage)
        return {
            "input_tokens": fresh + cached,
            "cached_input_tokens": cached,
            "cache_write_input_tokens": sum(u["cache_creation_input_tokens"] for u in usage),
            "output_tokens": output,
            "reasoning_output_tokens": int(output * 0.45),
        }


@dataclass(slots=True)
class CodexRateLimits:
    """The ``rate_limits`` envelope Codex writes onto every ``token_count``.

    Two windows, filling as the session works — which is what makes the demo's
    Codex subscription genuinely real rather than fixture-shaped: nothing fakes
    the read, the provider tail-reads this exactly as it reads the CLI's own.
    `window_minutes` carries the duration because the KEY is only a label (a
    `primary` reporting 10080 minutes is a real shape on a real host), and
    `plan_type` rides the same block as the windows it describes.
    """

    session_used: float
    weekly_used: float
    session_resets_at: int
    weekly_resets_at: int
    plan: str = "plus"

    @classmethod
    def draw(cls, rng: random.Random, base: datetime) -> CodexRateLimits:
        return cls(
            session_used=rng.uniform(4.0, 55.0),
            weekly_used=rng.uniform(12.0, 78.0),
            session_resets_at=int((base + timedelta(minutes=rng.randint(40, 300))).timestamp()),
            weekly_resets_at=int((base + timedelta(hours=rng.randint(8, 168))).timestamp()),
        )

    def spend(self) -> dict[str, Any]:
        self.session_used = min(99.0, self.session_used + 0.7)
        self.weekly_used = min(99.0, self.weekly_used + 0.1)
        return {
            "primary": {
                "used_percent": round(self.session_used, 1),
                "window_minutes": 300,
                "resets_at": self.session_resets_at,
            },
            "secondary": {
                "used_percent": round(self.weekly_used, 1),
                "window_minutes": 10080,
                "resets_at": self.weekly_resets_at,
            },
            "plan_type": self.plan,
        }


@dataclass(slots=True)
class SessionTimeline:
    """One session's RNG, clock and prompt cache, seeded from its id."""

    session_id: str
    rng: random.Random
    clock: SessionClock
    context: ContextWindow
    _base: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def of(cls, session_id: str, *, base: datetime, tempo: Tempo) -> SessionTimeline:
        rng = random.Random(session_id)
        return cls(
            session_id=session_id,
            rng=rng,
            clock=SessionClock(at=base, rng=rng, tempo=tempo),
            context=ContextWindow(rng=rng, cap=tempo.context_cap(rng)),
            _base=base,
        )

    def uuid(self) -> str:
        return rng_uuid(self.rng)

    def hex_id(self, prefix: str) -> str:
        return f"{prefix}-{self.rng.getrandbits(32):08x}"

    def rate_limits(self) -> CodexRateLimits:
        return CodexRateLimits.draw(self.rng, self._base)
