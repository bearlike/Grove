"""The size a Grove tmux session starts at while nothing is attached to it.

A session created detached has no client to take its dimensions from, so tmux
falls back to 80x24 — an aspect ratio no current terminal has. That shape is
what the agent lays its first screen out for, and what every `capture-pane`
consumer reads FOREVER: the web dashboard's terminal pane and every `peek` never
attach a client at all, so `window-size latest` never fires for them.

The rule these pin: a size is applied where nothing can supply one, and withheld
where a human's own terminal will. So a detached start carries the geometry and
an ATTACHING one deliberately does not — passing it there would be Grove
overriding the terminal the person is sitting at.
"""

from __future__ import annotations

import pytest

from grove.core import tmux
from grove.core.config import GroveConfig
from grove.core.container_tmux import TmuxEntry


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("200x50", (200, 50)),
        ("80x24", (80, 24)),
        ("", None),
        ("   ", None),
        ("wide", None),
        ("200x", None),
        ("0x50", None),
        ("200x0", None),
    ],
)
def test_parse_size_reads_a_geometry_or_honestly_declines(
    given: str, expected: tuple[int, int] | None
) -> None:
    """One parser for both creation sites — the host's libtmux call and the
    container's raw argv — because a second copy is how the two come to start at
    different sizes."""
    assert tmux.parse_size(given) == expected


def test_the_default_is_not_tmuxs_own_and_is_overridable() -> None:
    """A sensible default rather than a pin: it is one config field, so a
    deployment whose terminals are a different shape says so once."""
    assert GroveConfig().tmux.detached_size == "200x50"
    assert GroveConfig.model_validate({"tmux": {"detached_size": "320x84"}}).tmux.detached_size == (
        "320x84"
    )
    assert GroveConfig.model_validate({"tmux": {"detached_size": ""}}).tmux.detached_size == ""


def test_a_malformed_size_is_refused_at_the_config_boundary() -> None:
    """Validated where it is defined, so a typo is a loud config error rather
    than a silently ignored value that leaves every session at 80x24."""
    with pytest.raises(ValueError, match="detached_size"):
        GroveConfig.model_validate({"tmux": {"detached_size": "1920 x 1080"}})


def test_a_detached_container_entry_carries_the_geometry() -> None:
    entry = TmuxEntry(command="/t", session="agent", detached=True, size="200x50")

    assert entry.tokens(("claude",)) == [
        "/t",
        "new-session",
        "-A",
        "-d",
        "-x",
        "200",
        "-y",
        "50",
        "-s",
        "agent",
        "claude",
    ]


def test_an_attaching_container_entry_does_not() -> None:
    """`-A` either finds a session — whose size belongs to the attaching client —
    or creates one this command is immediately a client of. Either way the human's
    terminal decides, and Grove must not pre-empt it."""
    entry = TmuxEntry(command="/t", session="agent", size="200x50")

    assert "-x" not in entry.tokens(("claude",))
