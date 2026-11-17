"""The live step a working loader shows, folded from real stream-json frames.

Frame shapes are the ones a native Claude Code 2.1.289 session printed on its
pane wire log (2026-10-10), trimmed to the keys the fold reads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from grove.core.agents.live_pulse import LivePulse

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _thinking(tokens: int) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "thinking_tokens",
        "estimated_tokens": tokens,
        "estimated_tokens_delta": 50,
    }


def _assistant(block: dict[str, Any], *, msg: str = "m1", output: int = 10) -> dict[str, Any]:
    return {
        "type": "assistant",
        "parent_tool_use_id": None,
        "message": {"id": msg, "content": [block], "usage": {"output_tokens": output}},
    }


def _tool_use(tool_id: str, name: str, **tool_input: object) -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def _result(tool_id: str) -> dict[str, Any]:
    return {
        "type": "user",
        "parent_tool_use_id": None,
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]},
    }


def test_thinking_tokens_name_the_step_and_carry_the_estimate() -> None:
    pulse = LivePulse()
    pulse.observe(_thinking(150), _at(0))
    pulse.observe(_thinking(400), _at(1))

    view = pulse.view()
    assert view.step == "thinking"
    assert view.thinking_tokens == 400
    # The step's clock starts when thinking began, not at the latest estimate.
    assert view.since == _at(0)


def test_a_root_tool_call_is_in_flight_until_its_result_arrives() -> None:
    pulse = LivePulse()
    pulse.observe(_assistant(_tool_use("t1", "Bash", command="make lint\nmore")), _at(0))

    view = pulse.view()
    assert (view.step, view.tool, view.tool_detail) == ("tool", "Bash", "make lint")

    pulse.observe(_assistant(_tool_use("t9", "Read", file_path="/repo/src/app.py")), _at(1))
    assert pulse.view().tool_detail == "app.py"
    pulse.observe(_result("t9"), _at(2))

    pulse.observe(_result("t1"), _at(5))
    view = pulse.view()
    assert view.step == "thinking"
    assert view.tool is None


def test_parallel_tools_stay_in_flight_until_the_last_returns() -> None:
    pulse = LivePulse()
    pulse.observe(_assistant(_tool_use("t1", "Read", file_path="/a.py")), _at(0))
    pulse.observe(_assistant(_tool_use("t2", "Grep", pattern="needle")), _at(0))
    pulse.observe(_result("t2"), _at(1))

    assert (pulse.view().step, pulse.view().tool) == ("tool", "Read")
    pulse.observe(_result("t1"), _at(2))
    assert pulse.view().step == "thinking"


def test_output_tokens_sum_each_response_once_however_many_frames_it_spans() -> None:
    """One API response is written as one frame PER content block, each carrying
    the same usage; summing frames would multiply the count."""
    pulse = LivePulse()
    pulse.observe(_assistant({"type": "thinking", "thinking": ""}, msg="m1", output=900), _at(0))
    pulse.observe(_assistant({"type": "text", "text": "hi"}, msg="m1", output=900), _at(1))
    pulse.observe(_assistant({"type": "text", "text": "more"}, msg="m2", output=100), _at(2))

    assert pulse.view().output_tokens == 1000
    assert pulse.view().step == "generating"


def test_a_subagent_is_the_step_while_no_root_tool_is_in_flight() -> None:
    pulse = LivePulse()
    started = {
        "type": "system",
        "subtype": "task_started",
        "task_id": "a1",
        "description": "Inventory file fields",
        "subagent_type": "Explore",
    }
    progress = {
        "type": "system",
        "subtype": "task_progress",
        "task_id": "a1",
        "description": "Running List API package structure",
        "last_tool_name": "Bash",
    }
    pulse.observe(started, _at(0))
    pulse.observe(progress, _at(3))

    view = pulse.view()
    assert (view.step, view.subagents) == ("subagent", 1)
    assert (view.subagent, view.subagent_tool) == ("Running List API package structure", "Bash")

    pulse.observe({"type": "system", "subtype": "task_notification", "task_id": "a1"}, _at(9))
    assert pulse.view().subagents == 0
    assert pulse.view().step is None


def test_a_sidechain_frame_never_moves_the_root_step() -> None:
    pulse = LivePulse()
    side = _assistant(_tool_use("s1", "Bash", command="ls"))
    side["parent_tool_use_id"] = "toolu_parent"
    pulse.observe(side, _at(0))

    assert pulse.view().step is None
    assert pulse.view().tool is None


def test_a_result_frame_ends_the_turn() -> None:
    pulse = LivePulse()
    pulse.observe(_assistant(_tool_use("t1", "Bash", command="sleep 9")), _at(0))
    pulse.observe({"type": "result", "subtype": "success"}, _at(9))

    view = pulse.view()
    assert (view.step, view.tool, view.output_tokens) == (None, None, None)


def test_version_moves_only_on_a_visible_change() -> None:
    """The worker publishes on a version change; an unrelated frame must not
    cost a watched session a POST."""
    pulse = LivePulse()
    before = pulse.version
    pulse.observe({"type": "system", "subtype": "hook_started"}, _at(0))
    pulse.observe({"type": "control_response", "response": {}}, _at(0))
    assert pulse.version == before

    pulse.observe(_thinking(10), _at(1))
    assert pulse.version > before


def test_a_background_shell_task_is_not_a_subagent() -> None:
    """`task_*` frames also bracket `run_in_background` shell commands; on the
    live host 165 of 167 `task_started` frames were `local_bash`. Counting them
    would name every background build a "sub-agent"."""
    pulse = LivePulse()
    pulse.observe(
        {
            "type": "system",
            "subtype": "task_started",
            "task_id": "b1",
            "description": "Run the unit suite",
            "task_type": "local_bash",
        },
        _at(0),
    )
    pulse.observe(
        {"type": "system", "subtype": "task_progress", "task_id": "b1", "description": "x"},
        _at(1),
    )

    assert pulse.view().subagents == 0
    assert pulse.view().step is None
