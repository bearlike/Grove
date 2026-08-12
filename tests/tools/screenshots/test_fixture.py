"""The demo world validates itself, and says so out loud when it does not.

These pin the fixture's INVARIANTS, never its content: a screenshot's wording is
allowed to change every week, while "a per-ticket phase claim names a ticket
that is actually attached" must hold or the Info tab renders a claim about
nothing. Golden files would fail on the first copy edit and pin neither.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError
from tools.screenshots.fixture import (
    DEMO_PATH,
    DemoTicket,
    DemoWorld,
    Tempo,
    ToolStep,
    Transcript,
    Turn,
)

from grove.core.contracts.tickets import TicketRef
from grove.core.phase import PHASE_ORDER, TaskPhase


@pytest.fixture(scope="module")
def world() -> DemoWorld:
    return DemoWorld.load()


def test_the_shipped_fixture_loads(world: DemoWorld) -> None:
    assert world.workspaces
    assert world.history.curve.days == 365


def test_an_unknown_key_is_refused() -> None:
    """`extra="forbid"` is the whole reason a typo cannot silently drop content."""
    data: dict[str, Any] = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    data["workspaces"][0]["descriptoin"] = "typo"
    with pytest.raises(ValidationError, match="descriptoin"):
        DemoWorld.model_validate(data)


def test_a_phase_outside_the_engines_own_set_is_refused() -> None:
    data: dict[str, Any] = json.loads(DEMO_PATH.read_text(encoding="utf-8"))
    data["workspaces"][0]["phase"]["phase"] = "shipping"
    with pytest.raises(ValidationError):
        DemoWorld.model_validate(data)


def test_every_ticket_claim_names_an_attached_ticket(world: DemoWorld) -> None:
    """A claim keyed on a detached ticket is inert on the wire and invisible
    here, so it would be a screenshot silently missing a phase mark."""
    for entry in world.workspaces:
        for claim in entry.phase.tickets:
            assert claim.ticket in entry.ticket_keys, f"{entry.title}: {claim.ticket}"


def test_the_fixtures_ticket_key_is_the_engines_ticket_key(world: DemoWorld) -> None:
    """The join the webapp does client-side is `provider:id` on both sides."""
    for entry in world.workspaces:
        for ticket in entry.tickets:
            assert ticket.key == TicketRef(**ticket.model_dump()).key


def test_every_declared_workspace_lives_in_a_declared_repo(world: DemoWorld) -> None:
    assert {entry.repo for entry in world.workspaces} <= set(world.repos.names)


def test_every_phase_appears_somewhere_in_the_fleet(world: DemoWorld) -> None:
    """The palette is only visible in a screenshot if the fleet spends it."""
    claimed = {entry.phase.phase for entry in world.workspaces}
    assert claimed == set(get_args(TaskPhase)) == set(PHASE_ORDER)


def test_every_planted_model_has_a_price(world: DemoWorld) -> None:
    """An unpriced model makes the usage page's Cost tile read `unknown` — a
    range cost is all-or-unknown, so one missing row takes the whole tile."""
    planted = set(world.models.values())
    for table in world.history.models.values():
        planted.update(name for name, _ in table)
    assert planted <= set(world.pricing.models)


def test_codex_input_is_priced_at_zero(world: DemoWorld) -> None:
    """One non-zero Codex input rate makes every Codex session unpriceable —
    the projector nulls their `fresh_input` and `PriceBook` refuses a class with
    a rate and no count. See `ModelPrices` for the whole finding."""
    for name, price in world.pricing.models.items():
        if name.startswith("gpt-"):
            assert price.input == 0.0, name


def test_only_the_last_turn_may_end_open(world: DemoWorld) -> None:
    """An open call mid-transcript would leave a session reading WORKING from a
    turn that visibly finished."""
    for entry in world.workspaces:
        if entry.transcript is None:
            continue
        for turn in entry.transcript.turns[:-1]:
            assert turn.open_step is None, entry.title


def test_a_working_workspace_ends_its_transcript_open(world: DemoWorld) -> None:
    for entry in world.workspaces:
        if entry.transcript is not None and entry.working:
            assert entry.transcript.ends_open, entry.title


def test_a_codex_transcript_carries_no_ai_title(world: DemoWorld) -> None:
    """Claude Code writes an `ai-title` record; Codex has no equivalent."""
    for entry in world.workspaces:
        if entry.agent == "codex" and entry.transcript is not None:
            assert entry.transcript.ai_title is None, entry.title


def test_a_tool_steps_command_is_read_off_either_harnesses_argument() -> None:
    assert ToolStep(name="Bash", input={"command": "pytest -q"}).command == "pytest -q"
    assert ToolStep(name="exec_command", input={"cmd": "make lint"}).command == "make lint"
    assert ToolStep(name="Read", input={"file_path": "a.py"}).command is None
    assert ToolStep(name="apply_patch", input="*** Begin Patch\n").command is None


def test_a_patch_step_is_the_one_with_a_string_body() -> None:
    step = ToolStep(name="apply_patch", input="*** Begin Patch\n*** End Patch\n")
    assert step.is_patch
    assert not ToolStep(name="Edit", input={"file_path": "a.py"}).is_patch


def test_resolved_steps_stop_at_the_open_one() -> None:
    turn = Turn(
        prompt="p",
        tools=(
            ToolStep(name="Read", input={}, result="ok"),
            ToolStep(name="Bash", input={"command": "pytest"}),
            ToolStep(name="Read", input={}, result="never reached"),
        ),
    )
    assert [step.name for step in turn.resolved_steps()] == ["Read"]
    assert turn.open_step is not None and turn.open_step.name == "Bash"


def test_a_transcript_needs_at_least_one_turn() -> None:
    with pytest.raises(ValidationError):
        Transcript(turns=())


def test_a_shell_step_is_priced_by_its_leading_executable(world: DemoWorld) -> None:
    """The bash-command ranking ranks by TIME, so a corpus where every
    executable averages the same duration ranks by call count wearing a clock's
    clothes. `pytest` must be able to draw the slow span and `ls` must not."""
    tempo = world.tempo
    always_slow = Tempo(**{**tempo.model_dump(), "slow_odds": {"pytest": 1.0, "ls": 0.0}})
    rng = random.Random(0)
    pytest_step = ToolStep(name="Bash", input={"command": "pytest -q"})
    ls_step = ToolStep(name="Bash", input={"command": "ls -la src"})
    read_step = ToolStep(name="Read", input={"file_path": "a.py"})
    assert always_slow.tool_span(pytest_step, rng) == tempo.slow_tool_ms
    assert always_slow.tool_span(ls_step, rng) == tempo.tool_ms
    assert always_slow.tool_span(read_step, rng) == tempo.fast_tool_ms


def test_the_history_curve_is_a_pure_function_of_its_seed(world: DemoWorld) -> None:
    today = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
    first = world.history.curve.calendar(random.Random(world.history.seed), today=today)
    second = world.history.curve.calendar(random.Random(world.history.seed), today=today)
    assert [day.sessions for day in first] == [day.sessions for day in second]


def test_the_history_curve_hits_its_target_and_lights_most_of_the_year(
    world: DemoWorld,
) -> None:
    """A thin corpus renders the usage page as a product nobody uses, and a flat
    one argues nothing — so both the volume and the coverage are asserted."""
    curve = world.history.curve
    days = curve.calendar(random.Random(world.history.seed), today=datetime.now(tz=UTC))
    assert len(days) == curve.days
    assert sum(day.sessions for day in days) == curve.sessions
    lit = [day for day in days if day.lit]
    assert len(lit) / len(days) >= 0.80
    assert len({day.sessions for day in lit}) > 3, "every lit day is the same height"


def test_a_ticket_defaults_to_an_issue() -> None:
    ticket = DemoTicket(
        provider="gitea", id="1", title="t", url="https://example.invalid/1", status="open"
    )
    assert ticket.kind == "issue"
    assert ticket.key == "gitea:1"
