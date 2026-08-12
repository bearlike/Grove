"""`AgentAdapter.tool_version` and the shared `AgentVersionProbe`.

The contract this pins, in order of how much it matters:

1. The probe runs ONCE per resolved argv for the life of the process, however
   many launches ask — it is on every launch path and a subprocess per launch
   per agent is the cost the memo exists to remove.
2. It is best-effort in every direction a real host fails: a missing binary, a
   non-zero exit, empty output. A telemetry nicety must never fail a launch.
3. Output is recorded VERBATIM (the provider boundary — vendors spell their
   answer differently and Grove does not parse a semver out of it), with only
   defensive shaping.
4. Each adapter asks its OWN tool, and the two that cannot answer return `None`
   without running anything.
"""

from __future__ import annotations

import sys

import pytest

from grove.core.agents.base import AgentVersionProbe
from grove.core.agents.claude_code import ClaudeCodeAdapter
from grove.core.agents.codex import CodexAdapter
from grove.core.agents.generic import GenericAdapter
from grove.core.agents.mewbo import MewboAdapter

# Captured at import — BEFORE the autouse `_offline_tool_versions` fixture
# replaces the class attribute — so the handful of cases that mean to exercise
# the real subprocess boundary still can, without reaching for a private symbol.
_REAL_PROBE = AgentVersionProbe.probe


def _counting_probe(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[str, ...]], answer: str | None
) -> None:
    """Record every call at the probe's own public I/O seam, so what a test
    counts is subprocesses that WOULD have run."""

    def probe(cls: type[AgentVersionProbe], argv: tuple[str, ...]) -> str | None:
        calls.append(argv)
        return answer

    monkeypatch.setattr(AgentVersionProbe, "probe", classmethod(probe))


def test_the_probe_runs_once_per_binary_however_many_launches_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    _counting_probe(monkeypatch, calls, "codex-cli 0.147.0")

    answers = [AgentVersionProbe.version("codex --full-auto", "--version") for _ in range(5)]

    assert answers == ["codex-cli 0.147.0"] * 5
    assert calls == [("codex", "--version")]


def test_a_tool_that_cannot_answer_is_probed_once_and_then_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The negative answer is memoized too — otherwise every launch of an agent
    whose binary is absent pays a fresh failed subprocess forever."""
    calls: list[tuple[str, ...]] = []
    _counting_probe(monkeypatch, calls, None)

    assert AgentVersionProbe.version("claude", "--version") is None
    assert AgentVersionProbe.version("claude", "--version") is None
    assert len(calls) == 1


def test_the_memo_is_keyed_by_the_resolved_binary_not_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two agents differing only in launch flags share one answer; a genuinely
    different binary gets its own."""
    calls: list[tuple[str, ...]] = []
    _counting_probe(monkeypatch, calls, "1.0")

    AgentVersionProbe.version("codex --full-auto", "--version")
    AgentVersionProbe.version("codex --sandbox read-only", "--version")
    AgentVersionProbe.version("claude", "--version")

    assert calls == [("codex", "--version"), ("claude", "--version")]


@pytest.mark.parametrize(
    ("command", "binary"),
    [
        ("codex --full-auto", "codex"),
        ("  claude  ", "claude"),
        ('"/opt/my tools/claude" --model opus', "/opt/my tools/claude"),
        ("", ""),
        ('claude "unbalanced', ""),  # a hand-edited command must not raise
    ],
)
def test_binary_of_takes_the_executable_from_the_configured_command(
    command: str, binary: str
) -> None:
    assert AgentVersionProbe.binary_of(command) == binary


def test_an_empty_command_probes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    _counting_probe(monkeypatch, calls, "1.0")

    assert AgentVersionProbe.version("", "--version") is None
    assert calls == []


# ── the real subprocess boundary ────────────────────────────────────────────


def test_a_missing_binary_yields_none_and_never_raises() -> None:
    assert _REAL_PROBE(("grove-no-such-binary-abcdef", "--version")) is None


def test_a_nonzero_exit_yields_none() -> None:
    assert _REAL_PROBE((sys.executable, "-c", "raise SystemExit(3)")) is None


def test_output_is_recorded_verbatim() -> None:
    """No semver parsing: whatever the tool said, stripped, is the answer."""
    printed = "codex-cli 0.147.0"
    assert _REAL_PROBE((sys.executable, "-c", f"print({printed!r})")) == printed


def test_only_the_first_meaningful_line_is_kept_and_it_is_capped() -> None:
    """Defensive shaping, not interpretation: a banner or a runaway string must
    not ride onto every span the agent exports."""
    script = "print('');print('2.1.226 (Claude Code)');print('update available')"
    assert _REAL_PROBE((sys.executable, "-c", script)) == "2.1.226 (Claude Code)"

    long_script = f"print('v' * {AgentVersionProbe.MAX_LENGTH * 3})"
    answer = _REAL_PROBE((sys.executable, "-c", long_script))
    assert answer is not None
    assert len(answer) == AgentVersionProbe.MAX_LENGTH


def test_a_tool_printing_nothing_yields_none() -> None:
    assert _REAL_PROBE((sys.executable, "-c", "pass")) is None


# ── per-adapter dispatch ────────────────────────────────────────────────────


def test_each_cli_adapter_asks_its_own_configured_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    _counting_probe(monkeypatch, calls, "some version")

    assert ClaudeCodeAdapter().tool_version("claude --model opus") == "some version"
    assert CodexAdapter().tool_version("codex --full-auto") == "some version"

    assert calls == [("claude", "--version"), ("codex", "--version")]


def test_the_adapters_with_no_local_build_answer_none_without_probing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` is an answer here, not a degradation: mewbo's work runs on a
    backend and a generic command is a shell with no version flag to assume."""
    calls: list[tuple[str, ...]] = []
    _counting_probe(monkeypatch, calls, "should never be reached")

    assert MewboAdapter().tool_version("mewbo") is None
    assert GenericAdapter().tool_version("zsh") is None
    assert calls == []
