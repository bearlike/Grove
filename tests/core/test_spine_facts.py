"""Incremental reductions over an immutable normalized message spine.

The production change these tests protect is a cache that treats an equal-length
replacement as unchanged, or replays an unchanged prefix after an ordinary append.
Both break live facts: the former becomes stale; the latter spends poll time
re-walking historical payloads.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from grove.core.activity import _token_classes_of
from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.codex import CodexAdapter
from grove.core.agents.model import AgentMessage, ContentBlock, TokenUsage
from grove.core.session_duration import duration_of, generation_latency_of
from grove.core.spine_facts import SpineFactsCache

START = datetime(2026, 9, 16, 9, 0, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "agents" / "fixtures"
CLAUDE_SID = "11111111-1111-4111-8111-111111111111"
CLAUDE_CWD = Path("/home/dev/work/svc")
CODEX_SID = "019dd5d5-60fb-7461-bd07-b6e8cf342726"
CODEX_CWD = Path("/home/dev/work/svc")


def _at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


def _message(
    role: str,
    second: int,
    *,
    thread: str | None = None,
    content: tuple[ContentBlock, ...] = (),
    usage: TokenUsage | None = None,
) -> AgentMessage:
    return AgentMessage(
        role=role,  # type: ignore[arg-type]
        timestamp=_at(second),
        thread_id=thread,
        is_sidechain=thread is not None,
        content=content,
        usage=usage,
    )


def _synthetic_spine() -> tuple[AgentMessage, ...]:
    return (
        _message("user", 0),
        _message("assistant", 8, usage=TokenUsage(input=11, output=3)),
        _message(
            "assistant",
            9,
            content=(ContentBlock(type="tool_use", tool_name="Bash", tool_use_id="root"),),
        ),
        _message("user", 2, thread="sub"),
        _message("assistant", 6, thread="sub", usage=TokenUsage(cache_read=7, output=2)),
        _message(
            "assistant",
            5,
            thread="sub",
            content=(ContentBlock(type="tool_use", tool_name="Bash", tool_use_id="sub"),),
        ),
        _message(
            "tool",
            14,
            content=(ContentBlock(type="tool_result", tool_use_id="root"),),
        ),
        _message(
            "tool",
            12,
            thread="sub",
            content=(ContentBlock(type="tool_result", tool_use_id="sub"),),
        ),
    )


def _full(messages: tuple[AgentMessage, ...]) -> tuple[object, object, object]:
    return duration_of(messages), _token_classes_of(messages), generation_latency_of(messages)


def test_incremental_facts_match_full_reductions_for_interleaved_threads() -> None:
    """Incrementally paired root/subagent calls must retain the full reducers' result."""
    cache = SpineFactsCache()
    messages = _synthetic_spine()

    facts = cache.facts(messages)

    assert (facts.duration, facts.token_classes, facts.generation_latency) == _full(messages)
    assert facts.duration.active_ms == 14_000
    assert facts.duration.execution_ms == 24_000
    assert facts.duration.tool_ms == 12_000
    assert facts.generation_latency.calls == 2


def test_same_length_replacement_rebuilds_instead_of_returning_stale_facts() -> None:
    """A rewritten tail can retain length while changing every public reduction."""
    cache = SpineFactsCache()
    before = (
        _message("user", 0),
        _message("assistant", 4, usage=TokenUsage(input=10, output=1)),
    )
    after = (
        before[0],
        _message("assistant", 9, usage=TokenUsage(input=99, output=7)),
    )
    first = cache.facts(before)

    second = cache.facts(after)

    assert second is not first
    assert (second.duration, second.token_classes, second.generation_latency) == _full(after)
    assert second.duration.generation_ms == 9_000
    assert second.token_classes.fresh_input == 99


def test_out_of_order_insert_rebuilds_the_exact_full_result() -> None:
    """An insertion before a retained suffix is not an append and cannot reuse tail state."""
    cache = SpineFactsCache()
    prefix = (
        _message("user", 0),
        _message("assistant", 5, usage=TokenUsage(input=4, output=1)),
    )
    original = (*prefix, _message("user", 20), _message("assistant", 25))
    inserted = (*prefix, _message("user", 10), _message("assistant", 15), *original[2:])
    cache.facts(original)

    facts = cache.facts(inserted)

    assert (facts.duration, facts.token_classes, facts.generation_latency) == _full(inserted)
    assert facts.duration.generation_ms == 15_000


def test_append_processes_only_the_new_messages() -> None:
    """A long retained prefix is identity-checked but its payload is not reduced again."""
    cache = SpineFactsCache()
    prefix = tuple(
        _message("user" if index % 2 == 0 else "assistant", index) for index in range(1_000)
    )
    cache.facts(prefix)
    baseline = cache.folded_messages
    appended = (*prefix, _message("user", 1_000), _message("assistant", 1_001))

    cache.facts(appended)

    assert cache.folded_messages - baseline == 2
    assert cache.rebuilds == 1
    assert cache.incremental_updates == 1


def test_duplicate_tool_ids_match_the_existing_full_oracle() -> None:
    """A duplicated id retains the established full reducer's collision semantics."""
    cache = SpineFactsCache()
    messages = (
        _message(
            "assistant",
            0,
            thread="first",
            content=(ContentBlock(type="tool_use", tool_use_id="same"),),
        ),
        _message(
            "assistant",
            3,
            thread="second",
            content=(ContentBlock(type="tool_use", tool_use_id="same"),),
        ),
        _message(
            "tool",
            7,
            thread="first",
            content=(ContentBlock(type="tool_result", tool_use_id="same"),),
        ),
    )

    facts = cache.facts(messages)

    assert (facts.duration, facts.token_classes, facts.generation_latency) == _full(messages)


def test_current_claude_fixture_matches_the_full_reducers(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Real Claude split-block/continuation normalization remains equivalent to full facts."""
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))  # type: ignore[attr-defined]
    target = cfg / "projects" / _ClaudeHome.encode_cwd(CLAUDE_CWD) / f"{CLAUDE_SID}.jsonl"
    target.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "basic.jsonl", target)
    messages = ClaudeCodeAdapter().read_messages(CLAUDE_CWD, CLAUDE_SID)

    facts = SpineFactsCache().facts(messages)

    assert (facts.duration, facts.token_classes, facts.generation_latency) == _full(messages)


def test_current_codex_fixture_matches_the_full_reducers(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Real Codex dual-record normalization remains equivalent to full facts."""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))  # type: ignore[attr-defined]
    target = (
        home / "sessions" / "2026" / "04" / "28" / f"rollout-2026-04-28T13-43-44-{CODEX_SID}.jsonl"
    )
    target.parent.mkdir(parents=True)
    shutil.copyfile(FIXTURES / "codex_basic.jsonl", target)
    messages = CodexAdapter().read_messages(CODEX_CWD, CODEX_SID)

    facts = SpineFactsCache().facts(messages)

    assert (facts.duration, facts.token_classes, facts.generation_latency) == _full(messages)
