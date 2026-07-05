"""POST /workspaces/{id}/question-answer — the live-question answer surface (#109)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from grove.core.agents.hook import ClaudeHook
from grove.core.store import JsonWorkspaceStore
from grove.daemon import build_app
from tests.conftest import FakeTmux
from tests.daemon.conftest import daemon_test_config

_ASK_INPUT = {
    "questions": [
        {
            "question": "Which color?",
            "header": "Color",
            "multiSelect": False,
            "options": [{"label": "Blue"}, {"label": "Green"}],
        }
    ]
}


@pytest.fixture
def sidecar_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    target = tmp_path / "sidecars"
    monkeypatch.setattr("grove.core.paths.agent_sidecar_dir", lambda: target)
    return target


@pytest.fixture
def daemon(tmp_state_dir: Path, tmp_repo: Path, fake_tmux: FakeTmux) -> Iterator[TestClient]:
    del tmp_repo, fake_tmux
    store = JsonWorkspaceStore()
    app = build_app(cfg=daemon_test_config(), store=store)
    with TestClient(app) as client:
        yield client


def _create(daemon: TestClient, tmp_repo: Path) -> tuple[str, str]:
    """Create a workspace; return (workspace_id, minted agent_session_id).

    ``agent_session_id`` is off the wire (host-private), so it's read from a
    store pointed at the same sandboxed state file the daemon wrote."""
    ws_id = daemon.post(
        "/workspaces",
        json={
            "agent_name": "claude",
            "title": "answer test",
            "repo_root": str(tmp_repo),
            "branch_plan": {"kind": "auto"},
        },
    ).json()["id"]
    session_id = JsonWorkspaceStore().get(ws_id).agent_session_id
    assert session_id is not None
    return ws_id, session_id


def _capture(sidecar_dir: Path, session_id: str, tool_use_id: str = "toolu_1") -> None:
    ClaudeHook.record_event(
        {
            "hook_event_name": "PreToolUse",
            "session_id": session_id,
            "tool_name": "AskUserQuestion",
            "tool_use_id": tool_use_id,
            "tool_input": _ASK_INPUT,
        },
        sidecar_dir=sidecar_dir,
        tmux_pane=None,
        now=datetime.now(tz=UTC),
    )


def test_answer_question_204_and_drives_keys(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    ws_id, sid = _create(daemon, tmp_repo)
    _capture(sidecar_dir, sid)

    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={"session_id": sid, "tool_use_id": "toolu_1", "answers": [{"selected_indexes": [1]}]},
    )

    assert resp.status_code == 204
    assert resp.content == b""
    # Index 1 on a lone single-select → the digit "2" (no review step).
    assert [ops for _t, ops in fake_tmux.sent_keys] == [["2"]]


def test_answer_question_unknown_workspace_is_404(daemon: TestClient) -> None:
    resp = daemon.post(
        "/workspaces/nope/question-answer",
        json={"session_id": "s", "tool_use_id": "t", "answers": [{"selected_indexes": [0]}]},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_answer_question_foreign_session_id_is_409(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """A session_id that isn't this workspace's minted agent session is refused
    (#110) — otherwise a foreign or cross-workspace session_id could drive
    keystrokes into this workspace's pane."""
    ws_id, sid = _create(daemon, tmp_repo)
    foreign_sid = sid + "-foreign"
    _capture(sidecar_dir, foreign_sid)

    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={
            "session_id": foreign_sid,
            "tool_use_id": "toolu_1",
            "answers": [{"selected_indexes": [0]}],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "question_not_pending"
    assert fake_tmux.sent_keys == []


def test_answer_question_stale_tool_use_id_is_409(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    ws_id, sid = _create(daemon, tmp_repo)
    _capture(sidecar_dir, sid, tool_use_id="toolu_1")

    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={
            "session_id": sid,
            "tool_use_id": "toolu_STALE",
            "answers": [{"selected_indexes": [0]}],
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "question_not_pending"
    assert fake_tmux.sent_keys == []


def test_answer_question_no_capture_is_409(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    del sidecar_dir  # deliberately never written
    ws_id, sid = _create(daemon, tmp_repo)

    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={"session_id": sid, "tool_use_id": "toolu_1", "answers": [{"selected_indexes": [0]}]},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "question_not_pending"
    assert fake_tmux.sent_keys == []


def test_answer_question_plan_mismatch_is_422(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    ws_id, sid = _create(daemon, tmp_repo)
    _capture(sidecar_dir, sid)

    # Two answers for one captured question — a semantic mismatch (not a shape
    # error), so it's the manager's QuestionAnswerInvalid → 422, not Pydantic's.
    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={
            "session_id": sid,
            "tool_use_id": "toolu_1",
            "answers": [{"selected_indexes": [0]}, {"selected_indexes": [1]}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "question_answer_invalid"
    assert fake_tmux.sent_keys == []


def test_answer_question_structurally_invalid_body_is_422(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    ws_id, sid = _create(daemon, tmp_repo)
    _capture(sidecar_dir, sid)

    # An answer item that sets neither key — rejected by the wire model itself
    # (FastAPI's own 422), before the handler runs.
    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={"session_id": sid, "tool_use_id": "toolu_1", "answers": [{}]},
    )
    assert resp.status_code == 422
    assert fake_tmux.sent_keys == []


def test_answer_question_control_character_in_text_is_422(
    daemon: TestClient, tmp_repo: Path, fake_tmux: FakeTmux, sidecar_dir: Path
) -> None:
    """An ESC byte in free text would cancel the whole question if typed
    verbatim into the pane (#110) — rejected by the wire model before the
    handler runs, same as any other structurally invalid body."""
    ws_id, sid = _create(daemon, tmp_repo)
    _capture(sidecar_dir, sid)

    resp = daemon.post(
        f"/workspaces/{ws_id}/question-answer",
        json={"session_id": sid, "tool_use_id": "toolu_1", "answers": [{"text": "hi\x1bthere"}]},
    )
    assert resp.status_code == 422
    assert fake_tmux.sent_keys == []
