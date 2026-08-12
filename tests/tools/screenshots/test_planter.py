"""What the two writers put on disk, asserted as facts rather than as bytes.

A golden JSONL would fail on the first copy edit to `demo.json` and would still
not say WHY the file matters. These pin the four properties a downstream reader
actually depends on: every assistant message carries all four token classes (or
the session projects as unmeasured and the token heatmap goes blank), the
timestamps advance (or every tool call reads as taking the same time), an open
call has no matching result (or the session never reads WORKING), and the file
lands where the adapter globs for it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import pytest
from tools.screenshots.fixture import DemoWorld, Tempo, ToolStep, Transcript, Turn
from tools.screenshots.planter import ClaudeTranscriptPlanter, CodexRolloutPlanter

BASE = datetime(2026, 6, 13, 10, 0, tzinfo=UTC)
SESSION = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(scope="module")
def tempo() -> Tempo:
    return DemoWorld.load().tempo


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(
        ai_title="A demo session",
        turns=(
            Turn(
                prompt="Find the token read.",
                reply="Found it.",
                thinking="Grep first.",
                tools=(
                    ToolStep(name="Grep", input={"pattern": "token"}, result="one hit"),
                    ToolStep(
                        name="Bash",
                        input={"command": "pytest -q"},
                        result="1 failed",
                        is_error=True,
                    ),
                ),
            ),
            Turn(
                prompt="Fix it.",
                reply="",
                tools=(ToolStep(name="Bash", input={"command": "make lint"}),),
            ),
        ),
    )


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestClaudeTranscriptPlanter:
    def _plant(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript, cwd: Path | None = None
    ) -> list[dict]:
        planter = ClaudeTranscriptPlanter(root=tmp_path, tempo=tempo)
        cwd = cwd or tmp_path / "wt"
        path = planter.plant(
            cwd=cwd,
            session_id=SESSION,
            transcript=transcript,
            model="claude-opus-4-8",
            base=BASE,
        )
        assert path.parent == tmp_path / "projects" / planter.encode_cwd(cwd)
        return _records(path)

    def test_every_assistant_message_carries_all_four_token_classes(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        """The projection counts a session as MEASURED only when none is
        missing; omitting one leaves the daily token heatmap almost blank while
        the session and tool-call heatmaps over the same corpus light up, which
        is what makes it read as a rendering fault."""
        required = {
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        }
        assistants = [
            record
            for record in self._plant(tmp_path, tempo, transcript)
            if record["type"] == "assistant"
        ]
        assert assistants
        for record in assistants:
            assert required <= set(record["message"]["usage"]), record["uuid"]

    def test_timestamps_advance_by_varying_amounts(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        """These gaps ARE the durations the Activity card reports. A writer that
        stamped every line one minute apart plants a fleet where every tool call
        took exactly 60 s."""
        stamps = [
            datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
            for record in self._plant(tmp_path, tempo, transcript)
            if "timestamp" in record
        ]
        assert stamps == sorted(stamps)
        gaps = {round((b - a).total_seconds(), 3) for a, b in pairwise(stamps)}
        assert len(gaps) > 1

    def test_an_open_call_gets_no_result_and_no_closing_reply(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        records = self._plant(tmp_path, tempo, transcript)
        called = {
            block["id"]
            for record in records
            if record["type"] == "assistant"
            for block in record["message"]["content"]
            if block["type"] == "tool_use"
        }
        resolved = {
            block["tool_use_id"]
            for record in records
            if record["type"] == "user" and isinstance(record["message"]["content"], list)
            for block in record["message"]["content"]
            if block["type"] == "tool_result"
        }
        assert len(called - resolved) == 1
        assert records[-1]["message"]["stop_reason"] == "tool_use"

    def test_a_tool_failure_survives_onto_the_result(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        errors = [
            block["is_error"]
            for record in self._plant(tmp_path, tempo, transcript)
            if record["type"] == "user" and isinstance(record["message"]["content"], list)
            for block in record["message"]["content"]
            if block["type"] == "tool_result"
        ]
        assert errors == [False, True]

    def test_the_same_session_id_reproduces_the_same_file(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        """A transcript is a pure function of its session id, which is what lets
        the backfill reproduce byte for byte from one seed."""
        shared_cwd = tmp_path / "wt"
        first = self._plant(tmp_path / "a", tempo, transcript, shared_cwd)
        second = self._plant(tmp_path / "b", tempo, transcript, shared_cwd)
        assert first == second


class TestCodexRolloutPlanter:
    def _plant(self, tmp_path: Path, tempo: Tempo, transcript: Transcript) -> list[dict]:
        path = CodexRolloutPlanter(root=tmp_path, tempo=tempo).plant(
            cwd=tmp_path / "wt",
            session_id=SESSION,
            branch="feat/demo",
            transcript=transcript,
            model="gpt-5.1-codex",
            base=BASE,
        )
        assert path.parent == tmp_path / "sessions" / "2026" / "06" / "13"
        assert path.name.endswith(f"{SESSION}.jsonl")
        return _records(path)

    def test_conversation_and_status_stay_on_separate_record_types(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        """Conflating the two is the trap the real adapter guards against, so
        this writer keeps them apart on the way in too."""
        records = self._plant(tmp_path, tempo, transcript)
        kinds = {record["type"] for record in records}
        assert kinds == {"session_meta", "turn_context", "response_item", "event_msg"}
        for record in records:
            if record["type"] == "event_msg":
                assert record["payload"]["type"] in {
                    "task_started",
                    "task_complete",
                    "token_count",
                }

    def test_the_open_turn_never_completes(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        records = self._plant(tmp_path, tempo, transcript)
        started = {
            record["payload"]["turn_id"]
            for record in records
            if record["payload"].get("type") == "task_started"
        }
        completed = {
            record["payload"]["turn_id"]
            for record in records
            if record["payload"].get("type") == "task_complete"
        }
        assert len(started - completed) == 1

    def test_every_token_count_carries_the_rate_limit_envelope(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        """This block is the whole evidence base the Codex quota provider
        tail-reads, so the demo's Codex subscription is genuinely real."""
        counts = [
            record["payload"]
            for record in self._plant(tmp_path, tempo, transcript)
            if record["payload"].get("type") == "token_count"
        ]
        assert counts
        for payload in counts:
            limits = payload["rate_limits"]
            assert {"primary", "secondary", "plan_type"} <= set(limits)
            assert limits["primary"]["window_minutes"] == 300
            assert limits["secondary"]["window_minutes"] == 10080

    def test_the_provider_total_is_cumulative(
        self, tmp_path: Path, tempo: Tempo, transcript: Transcript
    ) -> None:
        totals = [
            record["payload"]["info"]["total_token_usage"]["input_tokens"]
            for record in self._plant(tmp_path, tempo, transcript)
            if record["payload"].get("type") == "token_count"
        ]
        assert totals == sorted(totals)
        assert len(set(totals)) == len(totals)

    def test_a_patch_body_is_recorded_verbatim_not_as_json(
        self, tmp_path: Path, tempo: Tempo
    ) -> None:
        """Codex records `apply_patch` as a custom tool call whose input is the
        raw patch body — encoding it as JSON is what the fixture's string-typed
        `ToolStep.input` exists to keep possible."""
        body = "*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** End Patch\n"
        records = self._plant(
            tmp_path,
            tempo,
            Transcript(
                turns=(
                    Turn(
                        prompt="patch it",
                        reply="done",
                        tools=(ToolStep(name="apply_patch", input=body, result="Applied"),),
                    ),
                )
            ),
        )
        calls = [r["payload"] for r in records if r["payload"].get("type") == "custom_tool_call"]
        assert [call["input"] for call in calls] == [body]
