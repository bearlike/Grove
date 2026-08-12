"""``ToolCall`` — request, response, duration and the running state.

The provider-neutral tool-invocation payload every transcript renderer draws an
expander from. What is pinned here is the SEAM, not either adapter: ``ToolCall``
and ``tool_outcomes`` are the single definition of "has this call come back",
so a Claude ``tool_result`` and a Codex ``function_call_output`` reach it as the
same shape and neither harness gets its own rule. The per-adapter wiring is
pinned in test_claude_code.py / test_codex.py; the wire cap lives in
tests/core/contracts/test_sessions_views.py.

Facts pinned from real on-host transcripts (2026-08-11): a Claude session left
mid-tool holds a ``tool_use`` whose ``tool_result`` has not been written, and a
Codex rollout holds an open ``function_call`` with no output for its
``call_id`` — the same unresolved shape, which is why "running" needs no
provider branch.
"""

from __future__ import annotations

from datetime import UTC, datetime

from grove.core.agents import AgentMessage, ContentBlock, ToolCall, ToolOutcome, tool_outcomes

T0 = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC)


def _call(name: str = "Bash", call_id: str = "t1", **kw: object) -> ContentBlock:
    return ContentBlock(
        type="tool_use",
        tool_name=name,
        tool_use_id=call_id,
        tool_input=kw or {"command": "ls"},
    )


def _result(call_id: str, text: str | None, *, is_error: bool = False) -> ContentBlock:
    return ContentBlock(type="tool_result", tool_use_id=call_id, text=text, is_error=is_error)


# ─── tool_outcomes: the one pre-scan ──────────────────────────────────────────


def test_outcomes_carry_body_error_flag_and_the_result_message_clock() -> None:
    messages = (
        AgentMessage(role="assistant", content=(_call(),), timestamp=T0),
        AgentMessage(
            role="tool",
            content=(_result("t1", "boom", is_error=True),),
            timestamp=T0.replace(second=3),
        ),
    )
    outcome = tool_outcomes(messages)["t1"]
    assert (outcome.text, outcome.is_error, outcome.at) == ("boom", True, T0.replace(second=3))


def test_a_result_with_no_body_is_still_a_resolved_call() -> None:
    """Membership, not truthiness — a tool that returned nothing has come back."""
    messages = (AgentMessage(role="tool", content=(_result("t1", None),)),)
    outcomes = tool_outcomes(messages)
    assert "t1" in outcomes
    assert ToolCall.from_block(_call(), outcomes).status == "ok"


def test_outcomes_include_sidechain_results() -> None:
    """A sub-agent's call resolves through its own result wherever it landed —
    the same non-filtering the question-resolution pre-scan always had."""
    messages = (AgentMessage(role="tool", content=(_result("t1", "done"),), is_sidechain=True),)
    assert tool_outcomes(messages)["t1"].text == "done"


# ─── status: running is a state, never the absence of a field ─────────────────


def test_an_unresolved_call_is_running_and_carries_its_request() -> None:
    call = ToolCall.from_block(_call(command="pytest -q"), {}, called_at=T0)
    assert call.status == "running"
    assert call.input == {"command": "pytest -q"}
    # No fabricated answer: a running call has measured nothing yet.
    assert call.result is None
    assert call.duration_ms is None


def test_a_resolved_call_reports_ok_with_its_response() -> None:
    outcomes = {"t1": ToolOutcome(text="2 passed", at=T0.replace(second=2))}
    call = ToolCall.from_block(_call(), outcomes, called_at=T0)
    assert (call.status, call.result, call.duration_ms) == ("ok", "2 passed", 2000)


def test_an_errored_result_reports_error_not_ok() -> None:
    outcomes = {"t1": ToolOutcome(text="Exit code 1", is_error=True, at=T0)}
    assert ToolCall.from_block(_call(), outcomes, called_at=T0).status == "error"


def test_an_id_less_block_can_never_correlate_so_it_reads_running() -> None:
    """Neither harness emits one, but the spine allows it — and a call nothing
    can resolve is honestly in flight rather than falsely settled.

    Safe because ``tool_outcomes`` only ever keys on a TRUTHY id, so the empty
    string it degrades to cannot collide with a real entry (asserted below)."""
    block = ContentBlock(type="tool_use", tool_name="X", tool_use_id=None, tool_input={})
    call = ToolCall.from_block(block, tool_outcomes(()))
    assert (call.tool_use_id, call.status) == ("", "running")


def test_outcomes_never_key_an_id_less_result() -> None:
    messages = (AgentMessage(role="tool", content=(_result("", "orphan"),)),)
    assert tool_outcomes(messages) == {}


# ─── duration: the pairing rule, shared with usage/_intervals ─────────────────


def test_duration_is_absent_when_either_endpoint_has_no_clock() -> None:
    outcomes = {"t1": ToolOutcome(text="ok", at=None)}
    assert ToolCall.from_block(_call(), outcomes, called_at=T0).duration_ms is None
    assert ToolCall.from_block(_call(), {"t1": ToolOutcome(at=T0)}).duration_ms is None


def test_an_inverted_pair_clamps_to_zero_rather_than_going_negative() -> None:
    """The two endpoints are written by two different clocks, so an end before
    its start is a clock artefact — the identical clamp ``ActiveIntervals.of``
    applies to the same intervals."""
    outcomes = {"t1": ToolOutcome(text="ok", at=T0)}
    assert ToolCall.from_block(_call(), outcomes, called_at=T0.replace(second=5)).duration_ms == 0


def test_a_measured_instant_stays_zero() -> None:
    outcomes = {"t1": ToolOutcome(text="ok", at=T0)}
    assert ToolCall.from_block(_call(), outcomes, called_at=T0).duration_ms == 0


# ─── concurrency: several calls in one message stay individually correlated ───


def test_parallel_calls_in_one_message_keep_their_own_result_and_duration() -> None:
    """Both harnesses issue several tool calls per assistant turn. They share a
    start (they were issued together, which is honest) and nothing else —
    correlation is by ``tool_use_id``, so an interleaved return never blurs.
    Measured on real Claude transcripts: parallel Bash pairs returning 3782 ms
    and 4246 ms from one message.
    """
    blocks = (_call("Bash", "a"), _call("Read", "b"), _call("Grep", "c"))
    outcomes = {
        "a": ToolOutcome(text="A", at=T0.replace(second=4)),
        "c": ToolOutcome(text="C", is_error=True, at=T0.replace(second=1)),
    }
    calls = [ToolCall.from_block(b, outcomes, called_at=T0) for b in blocks]
    assert [(c.name, c.status, c.duration_ms) for c in calls] == [
        ("Bash", "ok", 4000),
        ("Read", "running", None),
        ("Grep", "error", 1000),
    ]
    assert len({c.tool_use_id for c in calls}) == 3
