"""Claude Code's stream-json ``result`` frame projection.

**The payload below is a VERBATIM capture from a real ``claude -p
--output-format stream-json`` run (Claude Code 2.1.269, 2026-09-11)**, trimmed
only by deleting whole sibling keys. Two of its fields — ``ttft_ms`` and the
``subagent_stats.refused`` counts — have no transcript equivalent at all, so a
hand-built fixture would be inventing exactly the data this module exists to
capture.

Model ids are replaced with generic ones: the real capture named a private
gateway deployment, and this file is published.
"""

from __future__ import annotations

import json

from grove.core.agents.result_frame import (
    parse_fleet_census,
    parse_model_costs,
    parse_result_frame,
)

RESULT_FRAME = json.loads("""
{"type": "result", "subtype": "success", "is_error": false, "num_turns": 4,
 "session_id": "a3f67fc8-ef1d-47f0-8b07-029f50990382",
 "duration_ms": 12549, "duration_api_ms": 11067, "ttft_ms": 5230,
 "total_cost_usd": 0.133114, "stop_reason": "end_turn",
 "terminal_reason": "completed", "permission_denials": [],
 "result": "CCPROBE", "api_error_status": null,
 "usage": {"input_tokens": 19960, "cache_creation_input_tokens": 0,
           "cache_read_input_tokens": 32768, "output_tokens": 132,
           "service_tier": "standard"},
 "modelUsage": {
   "vendor-sonnet-5": {"inputTokens": 2, "outputTokens": 18,
     "cacheReadInputTokens": 0, "cacheCreationInputTokens": 1317,
     "costUSD": 0.01363, "contextWindow": 1000000, "maxOutputTokens": 32000,
     "thinkingTokens": 0, "provider": "firstParty"},
   "vendor-terra": {"inputTokens": 19960, "outputTokens": 132,
     "cacheReadInputTokens": 32768, "cacheCreationInputTokens": 0,
     "costUSD": 0.119484, "contextWindow": 983616, "maxOutputTokens": 32000,
     "thinkingTokens": 0, "provider": "firstParty"}},
 "subagent_stats": {"spawned": 0,
   "requested": {"background": 0, "foreground": 0, "unset": 0},
   "started_in_background": 0, "max_depth": 0, "spawned_by_subagents": 0,
   "completed": 0, "failed": 0,
   "killed": {"parent": 0, "user": 0, "system": 0},
   "refused": {"depth_limit": 0, "concurrency_limit": 0, "budget": 0},
   "by_type": {}}}
""")


def test_the_frame_yields_ttft_which_exists_in_no_transcript() -> None:
    """``ttft_ms`` is otherwise only on the beta OTel span or the wire.

    This is half the reason to harvest the frame at all: Grove's own guide records
    that suppressing the native trace exporter LOSES TTFT, and this is the one
    channel that returns it without re-enabling a second trace tree.
    """
    facts = parse_result_frame(RESULT_FRAME)

    assert facts is not None
    assert facts.ttft_ms == 5230
    assert facts.api_duration_ms == 11067
    assert facts.duration_ms == 12549


def test_per_model_cost_is_carried_as_the_harness_priced_it() -> None:
    """Carried, never recomputed — two numbers for one turn is worse than one.

    ``context_window`` in particular is a property of the deployment's routing
    that no local price table can know, and the two models here report genuinely
    different windows for a single session.
    """
    costs = parse_model_costs(RESULT_FRAME["modelUsage"])

    assert [c.model for c in costs] == ["vendor-sonnet-5", "vendor-terra"]
    assert costs[1].context_window == 983616
    assert costs[1].cache_read_tokens == 32768
    assert costs[0].cost_usd == 0.01363


def test_model_order_follows_the_frame_and_is_not_re_sorted() -> None:
    """The harness's own sequence survives.

    Re-sorting would impose an order the frame did not choose, and the first
    entry is meaningful: it is the model that ran first.
    """
    reordered = {"b-model": {"inputTokens": 1}, "a-model": {"inputTokens": 2}}

    assert [c.model for c in parse_model_costs(reordered)] == ["b-model", "a-model"]


def test_the_fleet_census_flattens_kill_and_refusal_REASONS() -> None:
    """The refusal counts are the half with no transcript equivalent.

    A subagent the harness declined to start leaves NO trace on disk, so without
    this "the fleet was capped" and "the fleet was small" are indistinguishable.
    Grove's own model carries a single ``active_subagents`` integer against this.
    """
    census = parse_fleet_census(
        {
            "spawned": 7,
            "completed": 4,
            "failed": 1,
            "max_depth": 2,
            "killed": {"parent": 1, "user": 1, "system": 0},
            "refused": {"depth_limit": 3, "concurrency_limit": 2, "budget": 1},
        }
    )

    assert census is not None
    assert census.spawned == 7
    assert census.max_depth == 2
    assert census.killed == 2
    assert census.refused == 6
    assert census.refused_budget == 1


def test_a_session_that_ran_no_subagent_reports_a_real_zero() -> None:
    """A count of zero is a genuine reading, unlike an absent measurement.

    The captured frame really did spawn nothing, and that must be expressible —
    which is why absence is the whole object being ``None`` rather than a
    nullable field per count.
    """
    facts = parse_result_frame(RESULT_FRAME)

    assert facts is not None
    assert facts.fleet is not None
    assert facts.fleet.spawned == 0
    assert facts.spawned_a_fleet is False


def test_an_absent_subagent_stats_block_is_None_not_an_empty_census() -> None:
    """A version that stops reporting the census must not read as an empty fleet.

    An all-zero ``FleetCensus`` claims a measured fleet of nothing; ``None``
    claims nothing was measured. They are different facts and a reader acts on
    them differently.
    """
    bare = {k: v for k, v in RESULT_FRAME.items() if k != "subagent_stats"}

    facts = parse_result_frame(bare)

    assert facts is not None
    assert facts.fleet is None
    assert facts.spawned_a_fleet is False


def test_permission_denials_arrive_as_a_LIST_and_are_counted_by_length() -> None:
    """The real frame carries the denials themselves, not a number.

    Pinned because the field name reads like a count, and an ``isinstance(int)``
    guard would silently report zero denials for every session that had some.
    """
    denied = json.loads(json.dumps(RESULT_FRAME))
    denied["permission_denials"] = [{"tool_name": "Bash"}, {"tool_name": "Write"}]

    facts = parse_result_frame(denied)

    assert facts is not None
    assert facts.permission_denials == 2


def test_an_unmeasured_duration_stays_None_while_a_count_defaults_to_zero() -> None:
    """The two helpers differ on purpose.

    A missing *count* means none happened; a missing *measurement* means nobody
    measured. Conflating them turns an unmeasured TTFT into a confident ``0 ms``.
    """
    partial = {"type": "result", "session_id": "s1", "subtype": "success"}

    facts = parse_result_frame(partial)

    assert facts is not None
    assert facts.ttft_ms is None
    assert facts.total_cost_usd is None
    assert facts.permission_denials == 0


def test_a_non_result_frame_is_ignored() -> None:
    """Only the terminal frame is a result; the stream carries many others.

    Measured types in one run: ``system`` (init/status/hook_started/hook_response),
    ``user``, ``assistant``, and six ``stream_event`` subtypes.
    """
    assert parse_result_frame({"type": "assistant", "session_id": "s1"}) is None
    assert parse_result_frame({"type": "stream_event", "session_id": "s1"}) is None
    assert parse_result_frame({"type": "result"}) is None


def test_an_unknown_subtype_is_carried_verbatim_and_never_rejected() -> None:
    """The subtype set is the harness's to extend.

    Refusing an unrecognized one would drop a whole real session's facts to
    defend a vocabulary Grove does not own — the provider boundary.
    """
    future = json.loads(json.dumps(RESULT_FRAME))
    future["subtype"] = "error_some_future_mode"
    future["is_error"] = True

    facts = parse_result_frame(future)

    assert facts is not None
    assert facts.subtype == "error_some_future_mode"
    assert facts.is_error is True


def test_a_boolean_is_never_accepted_as_a_measurement_or_a_count() -> None:
    """``bool`` is an ``int`` subclass at every one of these seams."""
    odd = json.loads(json.dumps(RESULT_FRAME))
    odd["ttft_ms"] = True
    odd["num_turns"] = True
    odd["total_cost_usd"] = True

    facts = parse_result_frame(odd)

    assert facts is not None
    assert facts.ttft_ms is None
    assert facts.num_turns is None
    assert facts.total_cost_usd is None
    assert parse_fleet_census({"spawned": True}) == parse_fleet_census({"spawned": 0})


def test_a_malformed_model_row_is_skipped_without_losing_its_siblings() -> None:
    """Tolerant per ENTRY, not per document.

    One renamed row must not cost the other models' cost data — the same
    granularity rule the phase reader's ``tickets`` map already follows.
    """
    mixed = {"good": {"inputTokens": 5, "costUSD": 1.0}, "bad": "not-an-object"}

    costs = parse_model_costs(mixed)

    assert [c.model for c in costs] == ["good"]
    assert costs[0].cost_usd == 1.0
