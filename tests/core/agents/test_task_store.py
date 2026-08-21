"""The LIVE task board on disk — ``_ClaudeTasks`` and ``latest_todo`` over it.

Claude Code's Task system keeps its board as one JSON file per task under
``<config>/tasks/<board>/<id>.json``; the transcript carries only the mutations
one participant made to it. The three things a transcript fold gets wrong are
pinned here against the real on-host shapes (``id``/``subject``/``status``/
``activeForm``, board dir ``session-<first 8 of the lead session id>``):

* a **deleted** task leaves no trace in any transcript, so the fold can only grow;
* an id is **re-issued** when the board restarts, merging two generations;
* a **shared** board's updates land in other participants' transcripts.

The fold itself stays the answer wherever no board exists (``TodoWrite``
sessions, sessions that never called a Task tool) — pinned here too, because the
board read must not take a checklist away from anything that had one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome

CWD = Path("/home/dev/work/tasks")
SID = "44444444-4444-4444-8444-444444444444"
# The board a session with no team gets: its own id, first 8 characters.
OWN_BOARD = "session-44444444"


@pytest.fixture
def adapter() -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter()


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A sandboxed Claude config dir — ``CLAUDE_CONFIG_DIR`` and ``Path.home``
    both inside ``tmp_path``, so neither the real transcripts nor the real task
    board are ever read."""
    cfg = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return cfg


def _transcript(claude_home: Path, lines: list[str], *, sid: str = SID) -> Path:
    """Install a main transcript where ``locate_transcripts(CWD, sid)`` finds it."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(CWD)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    return path


def _subagent(claude_home: Path, meta: dict[str, Any], *, sid: str = SID) -> None:
    """A sub-agent transcript plus its sidecar — the only record that names the
    team whose board this session shares."""
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(CWD) / sid / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "agent-worker.jsonl").write_text("", encoding="utf-8")
    (folder / "agent-worker.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _board(claude_home: Path, name: str, tasks: list[dict[str, Any]]) -> Path:
    """Write a board directory holding one file per task, as Claude Code does."""
    folder = claude_home / "tasks" / name
    folder.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        (folder / f"{task['id']}.json").write_text(json.dumps(task), encoding="utf-8")
    return folder


def _created(task_id: str, subject: str, tool_use_id: str, seq: int) -> list[str]:
    """The two transcript lines one ``TaskCreate`` writes: the call, then the
    ``tool_result`` that alone carries the assigned id."""
    return [
        json.dumps(
            {
                "type": "assistant",
                "uuid": f"a{seq}",
                "requestId": f"r{seq}",
                "isSidechain": False,
                "timestamp": f"2026-06-01T10:00:{seq:02d}.000Z",
                "message": {
                    "id": f"m{seq}",
                    "role": "assistant",
                    "stop_reason": "tool_use",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": "TaskCreate",
                            "input": {"subject": subject},
                        }
                    ],
                },
            }
        ),
        json.dumps(
            {
                "type": "user",
                "uuid": f"u{seq}",
                "isSidechain": False,
                "timestamp": f"2026-06-01T10:01:{seq:02d}.000Z",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": f"Task #{task_id} created successfully: {subject}",
                        }
                    ],
                },
            }
        ),
    ]


def _cwd_line() -> str:
    """A bare record carrying only ``cwd`` + ``timestamp`` — the minimum a
    sibling transcript needs for ``discover_paths`` to confirm it belongs to
    ``CWD`` (it filters on the recorded ``cwd``, not the folder name)."""
    return json.dumps({"cwd": str(CWD), "timestamp": "2026-06-01T09:00:00.000Z"})


def _three_creates() -> list[str]:
    return [
        *_created("1", "Scope the work", "tc1", 1),
        *_created("2", "Write the fix", "tc2", 2),
        *_created("3", "Run the gates", "tc3", 3),
    ]


def test_a_deleted_task_is_gone_although_its_taskcreate_is_still_in_the_transcript(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """THE regression. Removing a task deletes its file and emits no tool call
    at all, so a fold over ``TaskCreate`` calls can only ever grow — this is the
    shape that reported 84 items for a 58-item board on a real session. The
    board is the list; a superseded create contributes nothing."""
    _transcript(claude_home, _three_creates())
    _board(
        claude_home,
        OWN_BOARD,
        [
            {"id": "2", "subject": "Write the fix", "status": "in_progress"},
            {"id": "3", "subject": "Run the gates", "status": "pending"},
        ],
    )
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [(i.content, i.status) for i in todo.items] == [
        ("Write the fix", "in_progress"),
        ("Run the gates", "pending"),
    ]


def test_the_board_carries_updates_this_sessions_transcript_never_saw(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A shared board has several writers and each ``TaskUpdate`` lands in its
    own author's transcript, so a status this session never wrote still has to
    read true — the fold would report all three ``pending``."""
    _transcript(claude_home, _three_creates())
    _board(
        claude_home,
        OWN_BOARD,
        [
            {"id": "1", "subject": "Scope the work", "status": "completed"},
            {"id": "2", "subject": "Write the fix", "status": "completed"},
            {"id": "3", "subject": "Run the gates", "status": "in_progress"},
        ],
    )
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [i.status for i in todo.items] == ["completed", "completed", "in_progress"]


def test_the_board_reads_subject_and_active_form(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A stored task carries the same field names its ``TaskCreate`` payload
    did, which is why it folds through the same mapping."""
    _transcript(claude_home, _three_creates())
    _board(
        claude_home,
        OWN_BOARD,
        [
            {
                "id": "1",
                "subject": "Scope the work",
                "status": "in_progress",
                "activeForm": "Scoping the work",
                "owner": "worker",
                "blocks": [],
            }
        ],
    )
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [(i.content, i.status, i.active_form) for i in todo.items] == [
        ("Scope the work", "in_progress", "Scoping the work")
    ]


def test_numeric_ids_order_the_list(adapter: ClaudeCodeAdapter, claude_home: Path) -> None:
    """``#2`` sorts before ``#10``: the file name is the id, and a plain
    directory listing would put ``10.json`` first."""
    _transcript(claude_home, _three_creates())
    _board(
        claude_home,
        OWN_BOARD,
        [
            {"id": "10", "subject": "tenth", "status": "pending"},
            {"id": "2", "subject": "second", "status": "pending"},
        ],
    )
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [i.content for i in todo.items] == ["second", "tenth"]


def test_no_board_falls_back_to_the_transcript_fold(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A session on a build with no Task store, or one whose board this process
    cannot reach, keeps the checklist it had — the board read adds an answer, it
    never takes one away."""
    _transcript(claude_home, _three_creates())
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [i.content for i in todo.items] == ["Scope the work", "Write the fix", "Run the gates"]


def test_an_empty_board_directory_is_not_an_authoritative_empty_list(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Claude Code creates a board directory per session eagerly, so "the
    directory exists" would claim an empty list for every session on the host."""
    _transcript(claude_home, _three_creates())
    (claude_home / "tasks" / OWN_BOARD).mkdir(parents=True)
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert len(todo.items) == 3


def test_the_team_board_wins_over_the_sessions_own_name(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """A session id rotates in place (compaction, ``/clear``, a fork) while the
    team's board keeps the name the LEAD session started with, so the session's
    own id is not a reliable key for its board."""
    _transcript(claude_home, _three_creates())
    _subagent(claude_home, {"agentType": "worker", "teamName": "session-99999999"})
    _board(claude_home, OWN_BOARD, [{"id": "1", "subject": "stale", "status": "pending"}])
    _board(claude_home, "session-99999999", [{"id": "1", "subject": "live", "status": "pending"}])
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [i.content for i in todo.items] == ["live"]


def test_a_sibling_sessions_team_board_is_never_adopted_through_a_shared_cwd(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """THE cross-session regression. A cwd is not an identity: every ROOT-placement
    workspace in a repo scans the shared repo root, so the sessions recorded there
    belong to other workspaces and to hand-started agents. Resolution once widened
    to those siblings to rescue a rotated id, and on the reference host that served
    6 of 8 live workspaces a stranger's checklist — twice, the SAME 27-item board to
    two different workspaces. A session that names no team of its own has no board
    anyone can identify, so the fold over its OWN transcript answers."""
    lone_sid = "55555555-5555-5555-8555-555555555555"
    _transcript(claude_home, _three_creates(), sid=lone_sid)
    # A sibling session recorded for the SAME cwd, naming a team of its own.
    _transcript(claude_home, [_cwd_line()], sid=SID)
    _subagent(claude_home, {"agentType": "worker", "teamName": "session-99999999"}, sid=SID)
    _board(claude_home, "session-99999999", [{"id": "1", "subject": "theirs", "status": "pending"}])
    todo = adapter.latest_todo(CWD, lone_sid)
    assert todo is not None
    assert [i.content for i in todo.items] == ["Scope the work", "Write the fix", "Run the gates"]


def test_a_dead_pointer_session_reports_nothing_rather_than_a_strangers_board(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The extreme case: an id Grove still has pinned whose transcript is gone.
    It names no team and has no transcript to fold, so there is nothing to
    report — and the sibling recorded for this cwd is somebody else's."""
    dead_sid = "66666666-6666-6666-8666-666666666666"
    _transcript(claude_home, [_cwd_line()], sid=SID)
    _subagent(claude_home, {"agentType": "worker", "teamName": "session-99999999"}, sid=SID)
    _board(claude_home, "session-99999999", [{"id": "1", "subject": "theirs", "status": "pending"}])
    assert adapter.latest_todo(CWD, dead_sid) is None


def test_a_team_name_that_could_climb_out_of_the_tasks_dir_is_refused(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """The name arrives from a file Grove did not write, so a traversal-shaped
    one is not resolved at all — the session's own board answers instead."""
    _transcript(claude_home, _three_creates())
    _subagent(claude_home, {"teamName": "../../projects"})
    _board(claude_home, OWN_BOARD, [{"id": "1", "subject": "own board", "status": "pending"}])
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [i.content for i in todo.items] == ["own board"]


def test_an_unreadable_task_file_is_skipped_rather_than_raised(
    adapter: ClaudeCodeAdapter, claude_home: Path
) -> None:
    """Same defensive posture as every other read here: a half-written or
    malformed file drops out of the list and never breaks the poll."""
    _transcript(claude_home, _three_creates())
    folder = _board(claude_home, OWN_BOARD, [{"id": "1", "subject": "good", "status": "pending"}])
    (folder / "2.json").write_text("{not json", encoding="utf-8")
    (folder / ".highwatermark").write_text("1", encoding="utf-8")
    todo = adapter.latest_todo(CWD, SID)
    assert todo is not None
    assert [i.content for i in todo.items] == ["good"]
