"""`macros`: Grove commands declared in the config cascade, run as `/grove:<name>`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from grove.core.config import GroveConfig


def _cfg(macros: dict[str, object]) -> GroveConfig:
    return GroveConfig.model_validate({"macros": macros})


def test_a_command_is_a_named_list_of_slash_command_steps() -> None:
    cfg = _cfg({"compact-fast": {"steps": [" /model flash ", "/compact", "/model {model}"]}})
    assert cfg.macros["compact-fast"].steps == ["/model flash", "/compact", "/model {model}"]


@pytest.mark.parametrize(
    "steps",
    [
        [],
        ["compact"],  # not a slash command: it would reach the agent as prose
        ["/compact\n/model x"],  # one step is one line
        ["/grove:other"],  # a command may not run another Grove command
    ],
)
def test_a_step_that_is_not_one_agent_slash_command_is_refused(steps: list[str]) -> None:
    with pytest.raises(ValidationError):
        _cfg({"x": {"steps": steps}})


@pytest.mark.parametrize("name", ["Compact", "has space", "-lead", ""])
def test_a_command_name_must_survive_being_typed_after_the_prefix(name: str) -> None:
    with pytest.raises(ValidationError, match="lowercase slugs"):
        _cfg({name: {"steps": ["/compact"]}})
