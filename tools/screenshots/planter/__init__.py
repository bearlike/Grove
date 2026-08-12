"""Everything that writes the declared demo world onto disk.

Consumes validated `fixture` objects and produces the artifacts each adapter
expects to find: Claude Code JSONL, Codex rollouts, git repos and worktrees,
tmux sessions, phase files, store records, and a warm usage cache. Every side
effect in the screenshot pipeline that is not a screenshot lives here.

Collaborators are injected (a store, a config, the two writers) so a caller can
seed into a non-default path — the TUI capture does — and so a test can plant a
transcript without a fleet behind it.
"""

from .claude import ClaudeTranscriptPlanter
from .codex import CodexRolloutPlanter
from .config import DemoConfig
from .fleet import Fleet, FleetPlanter
from .history import HistoryPlanter
from .repo import GitTree
from .timeline import CodexRateLimits, ContextWindow, SessionClock, SessionTimeline, rng_uuid
from .tmux import DemoTmux
from .usage import UsageCacheWarmer

__all__ = [
    "ClaudeTranscriptPlanter",
    "CodexRateLimits",
    "CodexRolloutPlanter",
    "ContextWindow",
    "DemoConfig",
    "DemoTmux",
    "Fleet",
    "FleetPlanter",
    "GitTree",
    "HistoryPlanter",
    "SessionClock",
    "SessionTimeline",
    "UsageCacheWarmer",
    "rng_uuid",
]
