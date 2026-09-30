"""POST /workspaces/{id}/sessions/{sid}/feedback — a turn rating becomes Langfuse scores.

Reuses the sessions-endpoint fixtures (a sandboxed ``CLAUDE_CONFIG_DIR`` and a
store-backed workspace) and stubs only the Langfuse edge, so the turn → trace
join runs through the real adapter and the real replay derivation.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents import get_adapter
from grove.core.agents.claude_code import _ClaudeHome
from grove.core.config import GroveConfig
from grove.core.store import JsonWorkspaceStore
from grove.core.telemetry.feedback import (
    PRAISE_SCORE,
    REASON_SCORE,
    LangfuseScores,
    MissingScoreConfig,
    Rating,
    TurnFeedback,
    score_id,
)
from grove.core.trace import TraceInstrumentor
from grove.daemon import build_app
from tests.daemon.test_sessions_endpoints import _offline_mewbo, _state, claude_home, runtimes

__all__ = ["_offline_mewbo", "claude_home", "runtimes"]  # fixtures, re-exported for pytest

SID = "66666666-6666-4666-8666-666666666666"
FIRST = "2026-06-09T08:00:00.000Z"
SECOND = "2026-06-09T09:00:00.000Z"


def _line(kind: str, uuid: str, stamp: str, cwd: str, content: object) -> str:
    return json.dumps(
        {
            "type": kind,
            "uuid": uuid,
            "timestamp": stamp,
            "isSidechain": False,
            "cwd": cwd,
            "sessionId": SID,
            "message": {"role": kind, "content": content, "id": f"m-{uuid}", "model": "m"},
        }
    )


def _write_two_turns(home: Path, cwd: str) -> None:
    """Two complete turns: prompt → final answer, twice."""
    folder = home / "projects" / _ClaudeHome.encode_cwd(Path(cwd))
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        _line("user", "u1", FIRST, cwd, "first"),
        _line("assistant", "a1", FIRST, cwd, [{"type": "text", "text": "one"}]),
        _line("user", "u2", SECOND, cwd, "second"),
        _line("assistant", "a2", SECOND, cwd, [{"type": "text", "text": "two"}]),
    ]
    path = folder / f"{SID}.jsonl"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.utime(path, (2_000, 2_000))


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[tuple[TurnFeedback, tuple[str, ...]]]:
    calls: list[tuple[TurnFeedback, tuple[str, ...]]] = []
    monkeypatch.setattr(
        LangfuseScores,
        "record",
        lambda self, fb, catalogs: calls.append((fb, tuple(catalogs["negative"]))),
    )
    return calls


def _client(tmp_state_dir: Path, home: Path, *, telemetry: bool) -> TestClient:
    store = JsonWorkspaceStore()
    repo_root = tmp_state_dir / "repo-a"
    repo_root.mkdir()
    (repo_root / ".git").mkdir()
    state = _state("a1", str(repo_root), session_id=SID)
    store.save(state)
    _write_two_turns(home, state.worktree_path)
    cfg = GroveConfig.model_validate(
        {"auth": {"enabled": False}, "telemetry": {"enabled": telemetry}}
    )
    return TestClient(build_app(cfg=cfg, store=store))


@pytest.fixture
def langfuse_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.invalid")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")


@pytest.fixture
def client(
    tmp_state_dir: Path, claude_home: Path, runtimes: object, langfuse_env: None
) -> Iterator[TestClient]:
    with _client(tmp_state_dir, claude_home, telemetry=True) as c:
        yield c


def test_rating_lands_on_the_trace_the_replay_exports(
    client: TestClient,
    claude_home: Path,
    tmp_state_dir: Path,
    recorded: list[tuple[TurnFeedback, tuple[str, ...]]],
) -> None:
    """The SECOND turn — whose id is not the session-wide first-turn id — is
    the one that can only match if the join and the replay agree."""
    response = client.post(
        f"/workspaces/a1/sessions/{SID}/feedback",
        json={
            "started_at": SECOND,
            "rating": "negative",
            "reasons": ["Wasted time", "Wasted time"],
            "note": "  looped on tests  ",
        },
    )
    assert response.status_code == 204, response.text
    (feedback, catalog), *_ = recorded
    worktree = tmp_state_dir / "repo-a" / ".grove" / "worktrees" / "a1"
    # The replay's own manifests, oldest first — never `turn_trace_id`, which
    # is the code under test and would agree with itself.
    first_trace, second_trace = (
        f"{m.trace_id:032x}"
        for m in TraceInstrumentor(GroveConfig().telemetry).plan(
            worktree, SID, get_adapter("claude_code")
        )
    )
    assert feedback.trace_id == second_trace != first_trace
    assert feedback.rating == "negative"
    assert feedback.reasons == ("Wasted time",)
    assert feedback.note == "looped on tests"
    assert "Wasted time" in catalog


def test_refuses_without_langfuse(
    tmp_state_dir: Path, claude_home: Path, runtimes: object, recorded: list[object]
) -> None:
    with _client(tmp_state_dir, claude_home, telemetry=False) as c:
        body = c.get("/whoami").json()
        response = c.post(
            f"/workspaces/a1/sessions/{SID}/feedback",
            json={"started_at": FIRST, "rating": "positive"},
        )
    assert body["feedback_reasons"] == []
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "feedback_unavailable"
    assert recorded == []


def test_whoami_lists_reasons_when_langfuse_is_configured(client: TestClient) -> None:
    assert client.get("/whoami").json()["feedback_reasons"] == list(
        GroveConfig().telemetry.feedback_reasons
    )


def test_refuses_a_reason_the_config_does_not_list(
    client: TestClient, recorded: list[object]
) -> None:
    response = client.post(
        f"/workspaces/a1/sessions/{SID}/feedback",
        json={"started_at": FIRST, "rating": "negative", "reasons": ["Made up"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == {"error": "unknown_feedback_reason", "reasons": ["Made up"]}
    assert recorded == []


def test_unknown_turn_is_404(client: TestClient, recorded: list[object]) -> None:
    response = client.post(
        f"/workspaces/a1/sessions/{SID}/feedback",
        json={"started_at": "2026-06-09T10:00:00Z", "rating": "positive"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "turn_not_found"
    assert recorded == []


def test_scores_upsert_by_derived_id_and_withdraw_unticked_reasons() -> None:
    feedback = TurnFeedback(trace_id="t", rating="negative", reasons=("A",), note="why")
    verdict, reason = feedback.scores()
    assert verdict["value"] == 0 and verdict["dataType"] == "BOOLEAN"
    assert verdict["comment"] == "why"
    assert reason == {
        "id": score_id("t", REASON_SCORE, "A"),
        "traceId": "t",
        "name": REASON_SCORE,
        "dataType": "CATEGORICAL",
        "value": "A",
        "source": "ANNOTATION",
    }
    # A re-vote keeps the verdict's id, so it overwrites rather than stacks.
    positive = TurnFeedback(trace_id="t", rating="positive")
    assert positive.scores()[0]["id"] == verdict["id"]
    assert positive.scores()[0]["value"] == 1
    assert "comment" not in positive.scores()[0]
    catalogs: dict[Rating, tuple[str, ...]] = {"negative": ("A", "B"), "positive": ("P",)}
    assert positive.withdrawn(catalogs) == [
        score_id("t", REASON_SCORE, "A"),
        score_id("t", REASON_SCORE, "B"),
        score_id("t", PRAISE_SCORE, "P"),
    ]
    assert feedback.withdrawn(catalogs) == [
        score_id("t", REASON_SCORE, "B"),
        score_id("t", PRAISE_SCORE, "P"),
    ]


def test_a_thumbs_up_explains_itself_on_its_own_rubric() -> None:
    """Praise is a separate categorical rubric, so a category's meaning never
    depends on which way the thumb pointed."""
    praise = TurnFeedback(trace_id="t", rating="positive", reasons=("P",))
    _, reason = praise.scores()
    assert reason["name"] == PRAISE_SCORE
    assert reason["id"] == score_id("t", PRAISE_SCORE, "P")


def test_flipping_a_vote_withdraws_the_reasons_the_other_verdict_gave() -> None:
    """Rated down with a reason, then rated up: the down-vote's reason must be
    deleted, or the turn carries a complaint its rater has retracted."""
    flipped = TurnFeedback(trace_id="t", rating="positive", reasons=("P",))
    stale = flipped.withdrawn({"negative": ("A",), "positive": ("P",)})
    assert stale == [score_id("t", REASON_SCORE, "A")]


class _FakeLangfuse:
    """Stands in for `LangfuseScores._send`, the one HTTP edge."""

    def __init__(self, configs: dict[str, str]) -> None:
        self.configs = configs
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []

    def __call__(self, method: str, path: str, body: dict[str, object] | None) -> object:
        self.calls.append((method, path, body))
        if method == "GET":
            return {
                "data": [{"name": n, "id": i, "isArchived": False} for n, i in self.configs.items()]
            }
        return None


def test_record_grades_against_the_rubric_and_withdraws_unticked_reasons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLangfuse({"user-feedback": "cfg-bool", REASON_SCORE: "cfg-cat"})
    monkeypatch.setattr(LangfuseScores, "_send", lambda self, *a: fake(*a))
    feedback = TurnFeedback(trace_id="t", rating="negative", reasons=("A",))
    LangfuseScores("https://lf.invalid", "Basic x").record(
        feedback, {"negative": ("A", "B"), "positive": ()}
    )
    posts = [body for method, _, body in fake.calls if method == "POST"]
    assert [p["configId"] for p in posts if p] == ["cfg-bool", "cfg-cat"]
    # Langfuse accepts exactly one target per score; a sessionId beside the
    # traceId is a 400 (measured against a live instance).
    assert all(p and "sessionId" not in p for p in posts)
    deletes = [path for method, path, _ in fake.calls if method == "DELETE"]
    assert deletes == [f"/api/public/scores/{score_id('t', REASON_SCORE, 'B')}"]


def test_record_refuses_before_writing_when_a_rubric_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeLangfuse({"user-feedback": "cfg-bool"})
    monkeypatch.setattr(LangfuseScores, "_send", lambda self, *a: fake(*a))
    feedback = TurnFeedback(trace_id="t", rating="negative", reasons=("A",))
    with pytest.raises(MissingScoreConfig, match=REASON_SCORE):
        LangfuseScores("https://lf.invalid", "Basic x").record(
            feedback, {"negative": ("A",), "positive": ()}
        )
    # Nothing is written: a verdict without its reasons is a half-recorded rating.
    assert [method for method, _, _ in fake.calls] == ["GET"]


def test_an_open_turn_rates_onto_the_trace_it_will_export_when_it_closes(
    client: TestClient,
    claude_home: Path,
    tmp_state_dir: Path,
    recorded: list[tuple[TurnFeedback, tuple[str, ...]]],
) -> None:
    """A person flags waste while the agent is still working, so an OPEN turn
    (a trailing tool call, no final answer) must resolve — to the id the replay
    exports once that turn closes, never to a guess."""
    worktree = tmp_state_dir / "repo-a" / ".grove" / "worktrees" / "a1"
    path = claude_home / "projects" / _ClaudeHome.encode_cwd(worktree) / f"{SID}.jsonl"
    third = "2026-06-09T10:00:00.000Z"
    tool = [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}]
    open_turn = [
        _line("user", "u3", third, str(worktree), "third"),
        _line("assistant", "a3", third, str(worktree), tool),
    ]
    path.write_text(path.read_text() + "\n".join(open_turn) + "\n", encoding="utf-8")
    response = client.post(
        f"/workspaces/a1/sessions/{SID}/feedback",
        json={"started_at": third, "rating": "negative"},
    )
    assert response.status_code == 204, response.text
    closed = [_line("assistant", "a4", third, str(worktree), [{"type": "text", "text": "x"}])]
    path.write_text(path.read_text() + "\n".join(closed) + "\n", encoding="utf-8")
    *_, exported = TraceInstrumentor(GroveConfig().telemetry).plan(
        worktree, SID, get_adapter("claude_code")
    )
    assert recorded[-1][0].trace_id == f"{exported.trace_id:032x}"


def test_the_documented_rubrics_name_exactly_the_configured_reasons() -> None:
    """The docs page is where an operator copies the Langfuse registration from,
    and the daemon refuses any reason the config does not list — so a label
    renamed in one place and not the other is a rating that can never land."""
    page = (Path(__file__).parents[2] / "docs" / "features-telemetry.md").read_text()
    documented = set(re.findall(r'\{"label": "([^"]+)", "value": \d+\}', page))
    telemetry = GroveConfig().telemetry
    assert documented == {*telemetry.feedback_reasons, *telemetry.positive_feedback_reasons}
