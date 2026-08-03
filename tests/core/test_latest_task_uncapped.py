"""The task text has TWO readers with opposite cost profiles, and only one cap.

``AgentActivity.current_task`` rides the ~1 Hz activity delta for every
workspace on the host, plus every TUI row and webapp card — so every adapter
truncates it at ``_TASK_TEXT_CAP`` (500) at parse time, and uncapping it there
would put an arbitrarily large pasted prompt on the poll path once a second.
The issue-ops sticky comment renders the text ONCE per flush inside a collapsed
``<details>``, where the cap is pure loss.

So the wire field stays capped and a separate per-request seam answers uncapped
(``AgentAdapter.latest_task`` → ``WorkspaceManager.latest_task`` /
``latest_task_for``), exactly the split the todo axis already makes between the
counts on the tick and the full list behind ``latest_todo``. What this file
pins is that the two readers can never disagree about WHICH text they are
returning — the contrast (whole here, truncated there, same text) is the whole
point — and that the session resolution reaches past the mint, so codex (which
mints no id at all) is not silently excluded the way it once was from todo.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grove.core.agents.claude_code import ClaudeCodeAdapter, _ClaudeHome
from grove.core.agents.codex import CodexAdapter
from grove.core.config import GroveConfig
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import AgentSessionNotFound
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from tests.conftest import FakeTmux

_CAP = 500

LONG_TASK = (
    "Refactor the publisher so the sticky comment renders the whole task text. "
    + "The pasted brief continues at length. " * 40
).strip()


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _soon() -> datetime:
    """Births must postdate the workspace's own ``created_at`` or the adoption
    gate filters them as stale history — which looks exactly like a bug here."""
    return datetime.now(UTC) + timedelta(minutes=1)


@pytest.fixture
def cfg(tmp_path: Path) -> GroveConfig:
    return GroveConfig.model_validate(
        {
            "worktree": {"root_template": str(tmp_path / "trees"), "branch_prefix": "task/"},
            "tmux": {"session_prefix": "task-"},
            "hooks": {"enabled": False},
        }
    )


@pytest.fixture
def manager(
    tmp_repo: Path, fake_tmux: FakeTmux, cfg: GroveConfig, tmp_path: Path
) -> WorkspaceManager:
    del fake_tmux  # used via monkeypatch
    store = JsonWorkspaceStore(path=tmp_path / "state.json")
    return WorkspaceManager(repo_root=tmp_repo, cfg=cfg, store=store)


@pytest.fixture
def claude_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "claude-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


@pytest.fixture
def codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return home


def _write_claude(
    claude_home: Path,
    cwd: Path,
    session_id: str,
    *,
    human: str | None = None,
    last_prompt: str | None = None,
) -> None:
    """A minimal but REAL claude transcript, shapes per ``agents/CLAUDE.md``.

    ``human`` writes a real human turn; ``last_prompt`` writes the separate
    ``last-prompt`` record that outranks it. Either may be omitted, which is how
    the "carries no task text at all" case is built.
    """
    folder = claude_home / "projects" / _ClaudeHome.encode_cwd(cwd)
    folder.mkdir(parents=True, exist_ok=True)
    born = _iso(_soon())
    lines: list[dict[str, object]] = []
    if human is not None:
        lines.append(
            {
                "type": "user",
                "uuid": "u1",
                "timestamp": born,
                "isSidechain": False,
                "cwd": str(cwd),
                "message": {"role": "user", "content": human},
            }
        )
    lines.append(
        {
            "type": "assistant",
            "uuid": "a1",
            "requestId": "r1",
            "isSidechain": False,
            "cwd": str(cwd),
            "timestamp": born,
            "message": {
                "id": "m1",
                "role": "assistant",
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "on it"}],
            },
        }
    )
    if last_prompt is not None:
        lines.append(
            {
                "type": "last-prompt",
                "uuid": "lp1",
                "timestamp": born,
                "cwd": str(cwd),
                "lastPrompt": last_prompt,
            }
        )
    (folder / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )


def _write_codex(codex_home: Path, cwd: Path, session_id: str, human: str) -> None:
    """A minimal but REAL codex rollout: ``session_meta`` (the only record
    carrying the cwd) plus one ``response_item`` human message."""
    folder = codex_home / "sessions" / "2099" / "04" / "28"
    folder.mkdir(parents=True, exist_ok=True)
    born = _iso(_soon())
    lines = [
        {
            "timestamp": born,
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(cwd), "model_provider": "openai"},
        },
        {
            "timestamp": born,
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": human}],
            },
        },
    ]
    (folder / f"rollout-2099-04-28T13-43-44-{session_id}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )


# ─── the contrast: one text, two caps ───────────────────────────────────────


@pytest.mark.parametrize("via_last_prompt", [False, True], ids=["human-turn", "last-prompt"])
def test_the_seam_answers_whole_while_the_wire_field_stays_capped(
    manager: WorkspaceManager, claude_home: Path, via_last_prompt: bool
) -> None:
    """The reason this seam exists, in one assertion pair: the same session's
    ``current_task`` is truncated with a trailing ``…`` while ``latest_task``
    returns the text whole.

    Parametrized over BOTH branches of the selection rule, because the
    ``last-prompt`` record used to escape the cap entirely — so the poll path
    carried whatever a human pasted, and a test written only against a human
    turn would never have seen it.
    """
    assert len(LONG_TASK) > _CAP, "the fixture must actually exceed the cap"
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="long brief"))
    sid = state.agent_session_id
    assert sid is not None
    if via_last_prompt:
        _write_claude(claude_home, Path(state.agent_cwd), sid, human="go", last_prompt=LONG_TASK)
    else:
        _write_claude(claude_home, Path(state.agent_cwd), sid, human=LONG_TASK)

    full = manager.latest_task(state.id)
    capped = ClaudeCodeAdapter().parse_activity(Path(state.agent_cwd), sid).current_task

    assert full == LONG_TASK
    assert capped is not None
    assert len(capped) <= _CAP
    assert capped.endswith("…")
    # …and it is a prefix of the SAME text, not some other record's.
    assert LONG_TASK.startswith(capped[:-1].rstrip())


def test_both_reads_select_the_same_record(manager: WorkspaceManager, claude_home: Path) -> None:
    """A session carrying BOTH a human turn and a later ``last-prompt`` — the
    one case where the selection rule has to choose. Whichever it picks, the
    capped and uncapped reads must pick it together; they share one helper
    precisely so a future change to the rule cannot move one and not the other.
    """
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="two candidates"))
    sid = state.agent_session_id
    assert sid is not None
    _write_claude(
        claude_home,
        Path(state.agent_cwd),
        sid,
        human="the first thing I asked for",
        last_prompt="the newest thing I asked for",
    )

    full = manager.latest_task(state.id)
    capped = ClaudeCodeAdapter().parse_activity(Path(state.agent_cwd), sid).current_task

    assert full == "the newest thing I asked for"
    assert capped == full  # short enough that the cap is a no-op


def test_a_session_with_no_task_text_answers_none(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """No human turn, no ``last-prompt`` — an honest ``None``, not an error and
    not an empty string. The workspace HAS a session, so this is the answer that
    must stay distinguishable from the sessionless 404 below."""
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="assistant only"))
    sid = state.agent_session_id
    assert sid is not None
    _write_claude(claude_home, Path(state.agent_cwd), sid)

    assert manager.latest_task(state.id) is None


# ─── the two seams: 404 vs None, and resolution past the mint ───────────────


def test_the_id_seam_raises_where_the_state_seam_degrades(
    manager: WorkspaceManager, claude_home: Path
) -> None:
    """The ``latest_todo``/``latest_todo_for`` contract, applied again: a caller
    that NAMED a workspace is owed the difference between "no session" and "no
    task text yet", while a render path enumerating every workspace only ever
    wants "nothing to show"."""
    del claude_home  # sandboxes the scan; deliberately left empty
    state = manager.create(CreateWorkspaceRequest(agent_name="claude", title="sessionless"))
    sessionless = replace(state, agent_session_id=None)
    manager.store.save(sessionless)

    with pytest.raises(AgentSessionNotFound):
        manager.latest_task(state.id)
    assert manager.latest_task_for(sessionless) is None


def test_a_codex_workspace_resolves_through_discovery(
    manager: WorkspaceManager, codex_home: Path
) -> None:
    """codex mints no session id at all, so a mint-keyed read would 404 for an
    entire provider whose adapter reads the text fine — the exact bug the todo
    axis shipped with. Resolution falls through to the same adoption-gated
    discovery, and the poll seam (handed a session, never scanning) agrees."""
    state = manager.create(CreateWorkspaceRequest(agent_name="codex", title="no mint"))
    assert state.agent_session_id is None  # the premise
    sid = "019dd888-33cc-7461-bd07-ffffffffffff"
    _write_codex(codex_home, Path(state.agent_cwd), sid, LONG_TASK)
    # The adapter could always read it — only the engine's key was wrong.
    assert CodexAdapter().latest_task(Path(state.agent_cwd), sid) == LONG_TASK

    assert manager.latest_task(state.id) == LONG_TASK
    assert manager.latest_task_for(state, session_id=sid) == LONG_TASK
    # …and the ~1 Hz seam does NO discovery of its own: a scan per workspace per
    # tick is the daemon-CPU bug the transcript cache exists to prevent.
    assert manager.latest_task_for(state) is None
