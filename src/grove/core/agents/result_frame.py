"""Claude Code's stream-json ``result`` frame — the richest single payload either
harness emits, and the one Grove was throwing away.

One concern: **turn one terminal ``result`` frame into facts about a finished
session.** Nothing here launches anything, nothing here changes how a workspace
starts, and nothing here is required — it is a pure projection over a payload
Grove already receives wherever it runs Claude Code headless.

**Claude Code has no app server.** Measured 2026-09-11 against 2.1.269: zero
occurrences of ``app-server`` in the 219 MB binary, and ``claude server`` /
``connect`` / ``open`` / ``ssh`` are still unregistered. Its structured surface
is the SDK control protocol (``control_request``/``control_response``, with
``can_use_tool``, ``set_model``, ``interrupt``, ``set_permission_mode``,
``get_context_usage``), reachable only under
``-p --input-format stream-json --output-format stream-json`` — **which replaces
the Ink TUI**, so it can never back an attachable Grove workspace. That
constraint is why this module harvests rather than drives: it takes what
stream-json already yields, and the interactive launch path is untouched.

What the frame carries that nothing else does (all measured on one real turn):

``subagent_stats``
    A complete native fleet census — ``spawned``, ``completed``, ``failed``,
    ``killed{parent,user,system}``, ``refused{depth_limit,concurrency_limit,budget}``,
    ``max_depth`` — against Grove's single ``active_subagents`` integer. Grove
    reconstructs this today from three spawn flavours and three close rules, and
    ~26% of non-root subagents can never be attached to a spawn point from disk
    at all. **The refusal reasons have no transcript equivalent whatsoever.**

``ttft_ms``
    Time to first token, otherwise obtainable only from the beta OTel
    ``llm_request`` span or from the wire. Not in any transcript.

``total_cost_usd`` / ``modelUsage``
    Per-model cost, cache split and ``contextWindow``, already priced by the
    harness rather than by Grove's own price book.

``permission_denials`` / ``terminal_reason``
    Why a turn stopped, stated rather than inferred from a tail ``stop_reason``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

# A turn that ended because the harness refused something, rather than because
# the model finished. Kept as data rather than branched on: Grove reports what
# the frame said and never second-guesses it (the provider boundary).
RESULT_SUBTYPES: Final[tuple[str, ...]] = ("success", "error_max_turns", "error_during_execution")


@dataclass(slots=True, frozen=True)
class FleetCensus:
    """The native subagent census from one ``result`` frame.

    Every field is an ``int`` with a real zero — unlike a token measurement, a
    count of zero spawned subagents is a genuine reading, so there is nothing to
    distinguish from absence. Absence is represented by the whole object being
    ``None``.

    ``refused_*`` is the half with no transcript equivalent: a subagent the
    harness declined to start leaves no trace on disk, so "the fleet was capped"
    and "the fleet was small" are indistinguishable without this.
    """

    spawned: int = 0
    completed: int = 0
    failed: int = 0
    max_depth: int = 0
    killed_by_user: int = 0
    killed_by_system: int = 0
    killed_by_parent: int = 0
    refused_depth_limit: int = 0
    refused_concurrency_limit: int = 0
    refused_budget: int = 0

    @property
    def refused(self) -> int:
        """Every subagent the harness declined to start, for any reason."""
        return self.refused_depth_limit + self.refused_concurrency_limit + self.refused_budget

    @property
    def killed(self) -> int:
        """Every subagent stopped by somebody rather than by finishing."""
        return self.killed_by_user + self.killed_by_system + self.killed_by_parent


@dataclass(slots=True, frozen=True)
class ModelCost:
    """One model's usage and cost as the HARNESS priced it.

    Deliberately carried rather than recomputed. Grove's usage audit has its own
    price book, and two numbers for one turn that disagree is worse than one:
    this is the harness's own answer, and ``context_window`` in particular is a
    property of the deployment's routing that no local table can know.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cost_usd: float | None = None
    context_window: int | None = None


@dataclass(slots=True, frozen=True)
class ResultFacts:
    """Everything one terminal ``result`` frame says about a finished session.

    A projection, not a record: it is rebuilt from the frame whenever needed and
    never persisted, so a field added here costs no migration. ``None`` means
    *this frame did not say*, never a substituted zero.
    """

    session_id: str
    subtype: str
    is_error: bool = False
    num_turns: int | None = None
    duration_ms: int | None = None
    api_duration_ms: int | None = None
    ttft_ms: int | None = None
    total_cost_usd: float | None = None
    stop_reason: str | None = None
    terminal_reason: str | None = None
    permission_denials: int = 0
    fleet: FleetCensus | None = None
    models: tuple[ModelCost, ...] = ()

    @property
    def spawned_a_fleet(self) -> bool:
        """Whether this session ran any subagent at all."""
        return self.fleet is not None and self.fleet.spawned > 0


def parse_result_frame(frame: dict[str, Any]) -> ResultFacts | None:
    """Project one stream-json frame onto :class:`ResultFacts`; ``None`` if it is
    not a terminal result.

    Pure, and tolerant inward: a frame from a version that renamed or dropped a
    field loses that field rather than the whole read. The ``type`` check is the
    only hard requirement — everything else is a claim some release may stop
    making, and an unknown ``subtype`` is carried verbatim rather than rejected,
    because the set is the harness's to extend.
    """
    if frame.get("type") != "result":
        return None
    session_id = _text(frame.get("session_id"))
    if session_id is None:
        return None
    return ResultFacts(
        session_id=session_id,
        subtype=_text(frame.get("subtype")) or "unknown",
        is_error=frame.get("is_error") is True,
        num_turns=_int(frame.get("num_turns")),
        duration_ms=_int(frame.get("duration_ms")),
        api_duration_ms=_int(frame.get("duration_api_ms")),
        ttft_ms=_int(frame.get("ttft_ms")),
        total_cost_usd=_float(frame.get("total_cost_usd")),
        stop_reason=_text(frame.get("stop_reason")),
        terminal_reason=_text(frame.get("terminal_reason")),
        permission_denials=_count(frame.get("permission_denials")),
        fleet=parse_fleet_census(frame.get("subagent_stats")),
        models=parse_model_costs(frame.get("modelUsage")),
    )


def parse_fleet_census(stats: object) -> FleetCensus | None:
    """Project ``subagent_stats`` onto :class:`FleetCensus`; ``None`` if absent.

    The nested ``killed``/``refused`` objects are flattened, because the reason a
    subagent stopped is what a reader acts on and a two-level shape would make
    every consumer re-walk it. ``requested`` is deliberately dropped: it splits
    by background/foreground/unset, which describes how a spawn was *asked for*
    rather than what happened to it, and nothing Grove renders asks that.
    """
    if not isinstance(stats, dict):
        return None
    killed = stats.get("killed")
    killed = killed if isinstance(killed, dict) else {}
    refused = stats.get("refused")
    refused = refused if isinstance(refused, dict) else {}
    return FleetCensus(
        spawned=_count(stats.get("spawned")),
        completed=_count(stats.get("completed")),
        failed=_count(stats.get("failed")),
        max_depth=_count(stats.get("max_depth")),
        killed_by_user=_count(killed.get("user")),
        killed_by_system=_count(killed.get("system")),
        killed_by_parent=_count(killed.get("parent")),
        refused_depth_limit=_count(refused.get("depth_limit")),
        refused_concurrency_limit=_count(refused.get("concurrency_limit")),
        refused_budget=_count(refused.get("budget")),
    )


def parse_model_costs(usage: object) -> tuple[ModelCost, ...]:
    """Project ``modelUsage`` onto one :class:`ModelCost` per model.

    Ordered by the mapping's own iteration order, which is insertion order in
    every supported Python — so the frame's own sequence survives rather than
    being re-sorted into an order the harness did not choose.
    """
    if not isinstance(usage, dict):
        return ()
    costs: list[ModelCost] = []
    for model, row in usage.items():
        if not isinstance(row, dict) or not isinstance(model, str):
            continue
        costs.append(
            ModelCost(
                model=model,
                input_tokens=_count(row.get("inputTokens")),
                output_tokens=_count(row.get("outputTokens")),
                cache_read_tokens=_count(row.get("cacheReadInputTokens")),
                cache_creation_tokens=_count(row.get("cacheCreationInputTokens")),
                cost_usd=_float(row.get("costUSD")),
                context_window=_int(row.get("contextWindow")),
            )
        )
    return tuple(costs)


def _text(value: object) -> str | None:
    """A non-empty string, or ``None``."""
    return value.strip() or None if isinstance(value, str) else None


def _int(value: object) -> int | None:
    """An int, or ``None``. ``bool`` is excluded — it is an ``int`` subclass, and
    a ``True`` silently reading as ``1`` is a fabricated measurement."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _count(value: object) -> int:
    """A count, defaulting to ``0``.

    Distinct from :func:`_int` on purpose: a *count* has a meaningful zero and a
    missing one genuinely means none happened, whereas a missing *measurement*
    means nobody measured. Conflating the two is how an unmeasured duration
    becomes a confident zero. A list is counted by length — ``permission_denials``
    arrives as an array of the denials themselves.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, (list, tuple)):
        return len(value)
    return 0


def _float(value: object) -> float | None:
    """A float, or ``None``. ``bool`` excluded for :func:`_int`'s reason."""
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None
