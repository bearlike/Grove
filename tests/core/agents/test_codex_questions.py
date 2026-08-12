"""Codex's native ask-the-human tool, end to end through the real adapter.

Codex CLI asks the human through ``request_user_input`` — a plain
``function_call`` whose JSON-string ``arguments`` carry the same ``questions[]``
batch shape Claude's ``AskUserQuestion`` does, answered by a
``function_call_output`` holding ``{"answers": {<question id>: {"answers":
[...]}}}``. ``codex_request_user_input.jsonl`` is that exchange, shape-verbatim
from real on-host rollouts (scrubbed of host-private content) and pinned against
the tool schema carried by the installed codex-cli 0.147.0 binary.

The two facts these tests exist to hold down, both of which are the OPPOSITE of
the Claude case:

* Codex has no hook mechanism, so nothing can capture the ask at ask time — the
  transcript is the only source, and it works because the rollout is flushed
  record-by-record while the turn runs (measured live on 0.147.0).
* A pending question sits INSIDE an unfinished turn, so ``task_started`` has no
  matching ``task_complete`` and task pairing says WORKING. BLOCKED has to
  outrank it, or the one state that means "this workspace wants you" is never
  reachable for Codex.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from grove.core.agents import AgentActivityState
from grove.core.agents.codex import CodexAdapter
from grove.core.agents.model import AgentQuestion

FIXTURES = Path(__file__).parent / "fixtures"
RUI = FIXTURES / "codex_request_user_input.jsonl"

RUI_SID = "019dd9cc-44dd-7461-bd07-dddddddddddd"
RUI_CWD = Path("/home/dev/work/svc")

# The fixture line holding the answer. Slicing it off is exactly what the file
# looks like while the human is still being asked — the call record is already
# on disk and its output is not yet written.
_ANSWER_LINE = 5


@pytest.fixture
def adapter() -> CodexAdapter:
    return CodexAdapter()


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandboxed Codex home; ``CODEX_HOME`` + ``Path.home`` both point inside
    ``tmp_path`` so the adapter never touches the real ``~/.codex``."""
    home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _target(codex_home: Path) -> Path:
    target_dir = codex_home / "sessions" / "2026" / "04" / "28"
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir / f"rollout-2026-04-28T21-19-00-{RUI_SID}.jsonl"


def _install(codex_home: Path, adapter: CodexAdapter) -> Path:
    """The whole exchange — the question asked AND answered."""
    target = _target(codex_home)
    shutil.copyfile(RUI, target)
    adapter.clear_caches()
    return target


def _install_pending(codex_home: Path, adapter: CodexAdapter) -> Path:
    """The same bytes with the answer record removed — the file mid-question.

    Truncating the shipped fixture rather than shipping a second one keeps the
    two cases provably identical except for the one record whose presence IS the
    resolution signal.
    """
    lines = RUI.read_text(encoding="utf-8").splitlines()
    del lines[_ANSWER_LINE:]
    target = _target(codex_home)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    adapter.clear_caches()
    return target


# ─── recognition ─────────────────────────────────────────────────────────────


def test_request_user_input_is_a_recognized_question_tool() -> None:
    """One entry in ``QUESTION_TOOL_NAMES``, shared by every consumer — the
    status path and the turn renderer cannot disagree about what a question is."""
    assert AgentQuestion.recognizes("request_user_input")


def test_request_user_input_normalizes_through_the_shared_seam() -> None:
    """Codex's payload is Claude's ``AskUserQuestion`` payload plus a per-question
    ``id`` Grove deliberately does not adopt: resolution is group-level, so the
    answer-back address stays the positional ``<call_id>#<i>`` both providers use.
    No ``multiSelect`` key means ``single_select``, which is the truth — the CLI
    rejects a question with no options and adds the free-form choice client-side."""
    raw = {
        "questions": [
            {
                "id": "upload_input_format",
                "header": "Upload Input",
                "question": "Which input format?",
                "options": [{"label": "ENV", "description": "dotenv files"}, {"label": "JSON"}],
            }
        ]
    }

    (question,) = AgentQuestion.from_tool_call("request_user_input", raw, "call_rui1")

    assert question.id == "call_rui1#0"
    assert question.group_id == "call_rui1"
    assert question.kind == "single_select"
    assert question.multiselect is False
    assert question.source_tool == "request_user_input"
    assert question.header == "Upload Input"
    assert [o.label for o in question.options] == ["ENV", "JSON"]
    assert question.options[0].description == "dotenv files"


def test_claude_questions_are_unchanged_by_the_codex_entry() -> None:
    """The shared branch now keys off the recognizer rather than one literal
    name; ``AskUserQuestion`` must be byte-identical, ``source_tool`` included."""
    raw = {"questions": [{"question": "Pick one", "options": [{"label": "a"}, {"label": "b"}]}]}

    (question,) = AgentQuestion.from_tool_call("AskUserQuestion", raw, "call_1")

    assert question.source_tool == "AskUserQuestion"
    assert question.kind == "single_select"


# ─── rendering: the real adapter, the real rollout shape ────────────────────


def test_answered_batch_renders_as_resolved_question_entries(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """A batch yields N entries sharing one ``group_id``, each stamped with the
    group-level answer blob — the same resolution rule the Claude parser uses."""
    _install(codex_home, adapter)

    (turn,) = adapter.read_turns(RUI_CWD, RUI_SID)
    questions = [e.question for e in turn.entries if e.role == "question"]

    assert len(questions) == 2
    assert [q.id for q in questions if q] == ["call_rui1#0", "call_rui1#1"]
    assert [q.header for q in questions if q] == ["Upload Input", "Bulk Errors"]
    assert all(q and q.group_id == "call_rui1" for q in questions)
    assert all(q and q.answered for q in questions)
    assert all(q and "ENV + JSON + stdin (Recommended)" in (q.answer or "") for q in questions)


def test_an_answered_session_is_not_blocked(adapter: CodexAdapter, codex_home: Path) -> None:
    """Once the output lands the question is gone from the live axis and task
    pairing answers again — a resolved ask must never linger as BLOCKED."""
    activity = adapter.parse_activity(RUI_CWD, RUI_SID) if _install(codex_home, adapter) else None

    assert activity is not None
    assert activity.questions == ()
    assert activity.state is AgentActivityState.WAITING


# ─── status: BLOCKED, and it must outrank task pairing ──────────────────────


def test_a_pending_question_reads_blocked_and_carries_the_batch(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """The whole point. The fixture's ``task_started`` has no ``task_complete``,
    so task pairing says WORKING; the unanswered call must win, and the batch
    must ride the activity so a client can render an answer affordance."""
    _install_pending(codex_home, adapter)

    activity = adapter.parse_activity(RUI_CWD, RUI_SID)

    assert activity.state is AgentActivityState.BLOCKED
    assert activity.needs_attention
    assert [q.id for q in activity.questions] == ["call_rui1#0", "call_rui1#1"]
    assert [q.prompt for q in activity.questions] == [
        "Which input format should `upload` support first?",
        "How should `upload` behave when one key fails?",
    ]
    assert all(not q.answered for q in activity.questions)


def test_a_cancelled_question_resolves_rather_than_hanging(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """Codex writes an output record whatever happened — an answers object, an
    ``aborted by user after Ns`` string, or an unavailable-in-this-mode error
    (all three observed on-host). Any of them means the human is no longer being
    asked, so a cancel must clear BLOCKED exactly like an answer does."""
    target = _install_pending(codex_home, adapter)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(
            '{"timestamp":"2026-04-28T21:21:00.000Z","type":"response_item","payload":'
            '{"type":"function_call_output","call_id":"call_rui1",'
            '"output":"aborted by user after 95.0s"}}\n'
        )
    adapter.clear_caches()

    activity = adapter.parse_activity(RUI_CWD, RUI_SID)

    assert activity.questions == ()
    assert activity.state is not AgentActivityState.BLOCKED


def test_an_unparseable_question_call_does_not_block(
    adapter: CodexAdapter, codex_home: Path
) -> None:
    """A recognized name with a payload that normalizes to nothing falls through
    to the generic tool digest — reporting BLOCKED with no question to render
    would be a workspace the user is told to act on and cannot."""
    target = _target(codex_home)
    target.write_text(
        "\n".join(
            [
                '{"timestamp":"2026-04-28T21:19:00.000Z","type":"session_meta","payload":'
                '{"id":"' + RUI_SID + '","cwd":"/home/dev/work/svc","cli_version":"0.147.0"}}',
                '{"timestamp":"2026-04-28T21:19:01.000Z","type":"response_item","payload":'
                '{"type":"message","role":"user","content":[{"type":"input_text","text":"go"}]}}',
                '{"timestamp":"2026-04-28T21:19:20.000Z","type":"response_item","payload":'
                '{"type":"function_call","name":"request_user_input",'
                '"arguments":"{\\"questions\\":[]}","call_id":"call_junk"}}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    adapter.clear_caches()

    activity = adapter.parse_activity(RUI_CWD, RUI_SID)

    assert activity.questions == ()
    assert activity.state is not AgentActivityState.BLOCKED

    (turn,) = adapter.read_turns(RUI_CWD, RUI_SID)
    assert [(e.role, e.text) for e in turn.entries] == [("tool", "request_user_input")]
