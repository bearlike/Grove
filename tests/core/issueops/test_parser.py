"""CommandParser grammar — trigger match, verb set, free-text, usage.

Table-driven: the parser is pure, so one parametrized case per grammar rule is
the cheapest full-coverage shape. The trigger defaults to ``@grove`` but is
passed explicitly (it is config-cascaded data, never a constant the parser owns).
"""

from __future__ import annotations

import pytest

from grove.core.issueops import CommandParser, ParsedCommand

parser = CommandParser()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # ── not triggered: first token isn't the trigger (word-boundary) ──────
        ("please look at this", None),
        ("email @grovejohn about it", None),  # @grove is a prefix of a longer handle
        ("ping @grove later in the thread", None),  # trigger not the FIRST token
        ("", None),
        # ── clean verbs ──────────────────────────────────────────────────────
        ("@grove status", ParsedCommand(kind="verb", verb="status")),
        ("@grove pause", ParsedCommand(kind="verb", verb="pause")),
        ("@grove resume", ParsedCommand(kind="verb", verb="resume")),
        ("@grove stop", ParsedCommand(kind="verb", verb="stop")),
        ("  @grove   stop  ", ParsedCommand(kind="verb", verb="stop")),  # whitespace
        ("@grove STOP", ParsedCommand(kind="verb", verb="stop")),  # verb case-insensitive
        ("@GROVE stop", ParsedCommand(kind="verb", verb="stop")),  # trigger case-insensitive
        # ── free-text prompt: first word after the trigger is not a verb ──────
        ("@grove fix the flaky test", ParsedCommand(kind="prompt", text="fix the flaky test")),
        # a non-verb first word is JUST prompt text, even when a verb follows
        ("@grove please pause it", ParsedCommand(kind="prompt", text="please pause it")),
        # trailing/leading whitespace inside the prompt is normalized at the edges only
        ("@grove   rerun CI  ", ParsedCommand(kind="prompt", text="rerun CI")),
        # ── usage: a recognized verb with trailing junk, or an empty command ──
        ("@grove status now please", ParsedCommand(kind="usage", verb="status")),
        ("@grove stop it", ParsedCommand(kind="usage", verb="stop")),
        ("@grove", ParsedCommand(kind="usage")),
    ],
)
def test_parse(body: str, expected: ParsedCommand | None) -> None:
    result = parser.parse(body, trigger="@grove")
    if expected is None:
        assert result is None
        return
    assert result is not None
    assert result.kind == expected.kind
    assert result.verb == expected.verb
    assert result.text == expected.text


def test_usage_carries_a_reason() -> None:
    """A verb-with-junk usage names the offending verb so the reply is specific."""
    result = parser.parse("@grove stop everything", trigger="@grove")
    assert result is not None
    assert result.kind == "usage"
    assert result.verb == "stop"
    assert result.reason  # non-empty explanation for the reply


def test_trigger_is_configurable() -> None:
    """The trigger is data — a repo can re-point it without touching code."""
    assert parser.parse("/grove status", trigger="/grove") == ParsedCommand(
        kind="verb", verb="status"
    )
    # and the default trigger no longer matches once re-pointed
    assert parser.parse("@grove status", trigger="/grove") is None


def test_punctuation_after_trigger_is_not_a_word_boundary_violation() -> None:
    """``@grove:`` still triggers — punctuation ends the trigger token."""
    result = parser.parse("@grove: fix it", trigger="@grove")
    assert result is not None
    assert result.kind == "prompt"
