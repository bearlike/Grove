"""The synthetic demo fleet that backs every documentation screenshot.

Both screenshot tools build on this one module so the TUI captures
(`tools/screenshots/capture.py`) and the web captures
(`tools/screenshots/webapp_capture.py`) show the same fictional fleet:
two repos (`acme-api`, `acme-web`), a handful of workspaces in mixed
states, and hand-planted Claude-Code-style transcripts so the activity
readouts, transcript panels, and sessions browser render real recorded
turns instead of empty history.

Nothing here is host-specific. The repos live under `/tmp`, the agent
processes are small stub scripts that print realistic output then sleep,
and the transcripts are synthesized. Callers are responsible for pointing
``XDG_CONFIG_HOME`` / ``XDG_STATE_HOME`` / ``CLAUDE_CONFIG_DIR`` at a
throwaway tree BEFORE importing grove, so a run never touches the user's
real config, the real daemon, or real repos.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from grove.core import tmux as tmux_mod
from grove.core.config import GroveConfig
from grove.core.contracts.branch_plan import NewNamedBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import TmuxError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore
from grove.core.workspace import WorkspaceState

REPO_ROOT = Path(__file__).resolve().parents[2]
STUB_CLAUDE = REPO_ROOT / "tools" / "screenshots" / "agents" / "stub-claude.sh"
STUB_AIDER = REPO_ROOT / "tools" / "screenshots" / "agents" / "stub-aider.sh"

# Stable, fictional model id so the agent readout looks like a real session
# without leaking anything host-specific.
_DEMO_MODEL = "claude-opus-4-8"


def install_quiet_tmux() -> None:
    """Force demo panes to spawn a bare shell so no MOTD leaks into captures.

    Real `grove` invokes ``$SHELL`` for each new window. On a host with a
    chatty profile (a server MOTD, a system banner), ``capture-pane`` returns
    that instead of the agent stub's content. Spawning ``bash --noprofile
    --norc`` keeps the pane clean. Only relevant inside the screenshot run.
    """

    def _quiet_create_session(name: str, cwd: Path, *, history_limit: int = 50_000) -> None:
        server = tmux_mod._server()
        if tmux_mod.has_session(name):
            raise TmuxError(f"tmux session already exists: {name}")
        try:
            session = server.new_session(
                session_name=name,
                start_directory=str(cwd),
                attach=False,
                window_command="bash --noprofile --norc",
            )
        except Exception as exc:
            raise TmuxError(f"failed to create tmux session {name}: {exc}") from exc
        try:
            session.set_option("history-limit", str(history_limit))
            session.set_option("mouse", "on")
        except Exception:
            pass

    tmux_mod.create_session = _quiet_create_session


def _encode_cwd(cwd: Path) -> str:
    """Claude Code's transcript folder name for a worktree.

    Mirrors the adapter's forward encoding (lossy, non-reversible): every
    non-alphanumeric character becomes a dash. Inlined here so the tool does
    not depend on a private adapter helper.
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


# ─── git harness ─────────────────────────────────────────────────────────────


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def make_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "current")
    _git(repo, "config", "user.email", "demo@grove.local")
    _git(repo, "config", "user.name", "Grove Demo")
    (repo / "README.md").write_text(f"# {name}\n\nDemo repository.\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-verify")
    return repo.resolve()


def demo_config() -> GroveConfig:
    """Shipped defaults, but with the agent registry pointed at the stubs.

    The `claude` entry declares ``kind: "claude_code"`` so the introspection
    layer treats it as a real Claude session and reads the planted
    transcripts. `aider` and `shell` stay generic.
    """
    return GroveConfig.model_validate(
        {
            "tmux": {
                "session_prefix": "grove-",
                "activity_threshold_seconds": 3,
            },
            "agents": [
                {
                    "name": "claude",
                    "command": str(STUB_CLAUDE),
                    "kind": "claude_code",
                    "description": "Anthropic Claude Code",
                },
                {
                    "name": "aider",
                    "command": str(STUB_AIDER),
                    "description": "Aider AI pair-programmer",
                },
                {
                    "name": "shell",
                    "command": "$SHELL",
                    "description": "Plain shell",
                },
            ],
        }
    )


def _make_manager(repo: Path, store: JsonWorkspaceStore) -> WorkspaceManager:
    """A real-tmux manager bound to ``repo`` sharing ``store`` with its peers."""
    return WorkspaceManager(repo_root=repo, cfg=demo_config(), store=store)


def _dirty(worktree: Path) -> None:
    """Leave one uncommitted edit so the peek summary shows a dirty count."""
    readme = worktree / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nWIP.\n", encoding="utf-8")


def _ahead(worktree: Path, name: str) -> None:
    """Add one commit on the branch so the summary shows `ahead 1`."""
    (worktree / f"{name}.txt").write_text("scratch\n", encoding="utf-8")
    _git(worktree, "add", ".")
    _git(worktree, "commit", "-m", f"wip: {name}", "--no-verify")


# ─── transcript planting ─────────────────────────────────────────────────────


def _plant_transcript(
    *,
    worktree: Path,
    session_id: str,
    ai_title: str,
    turns: list[tuple[str, str, list[tuple[str, str]]]],
    working: bool,
) -> None:
    """Write a Claude-Code-style JSONL transcript for one demo session.

    ``turns`` is a list of ``(human_prompt, assistant_text, [(tool, result)])``
    triples. The final assistant message is left mid-tool-call when
    ``working`` is True (reads as WORKING) or closed with ``end_turn`` when
    False (reads as WAITING) — the same tail rule the real adapter uses.
    """
    cwd = str(worktree)
    folder = Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / _encode_cwd(worktree)
    folder.mkdir(parents=True, exist_ok=True)

    lines: list[dict[str, Any]] = [
        {"type": "ai-title", "aiTitle": ai_title, "sessionId": session_id}
    ]
    base = datetime(2026, 6, 13, 10, 0, 0, tzinfo=UTC)
    clock = 0
    in_tok = 0

    def stamp() -> str:
        nonlocal clock
        clock += 1
        return (base + timedelta(minutes=clock)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    for t_index, (prompt, reply, tools) in enumerate(turns):
        last_turn = t_index == len(turns) - 1
        in_tok += 1800
        lines.append(
            {
                "type": "user",
                "uuid": str(uuid.uuid4()),
                "timestamp": stamp(),
                "isSidechain": False,
                "cwd": cwd,
                "sessionId": session_id,
                "message": {"role": "user", "content": prompt},
            }
        )
        for tool, result in tools:
            tu_id = f"tu-{uuid.uuid4().hex[:8]}"
            lines.append(
                {
                    "type": "assistant",
                    "uuid": str(uuid.uuid4()),
                    "requestId": f"req-{uuid.uuid4().hex[:8]}",
                    "timestamp": stamp(),
                    "isSidechain": False,
                    "sessionId": session_id,
                    "message": {
                        "id": f"msg-{uuid.uuid4().hex[:8]}",
                        "role": "assistant",
                        "model": _DEMO_MODEL,
                        "stop_reason": "tool_use",
                        "usage": {
                            "input_tokens": 40,
                            "cache_read_input_tokens": in_tok,
                            "output_tokens": 120,
                        },
                        "content": [{"type": "tool_use", "id": tu_id, "name": tool, "input": {}}],
                    },
                }
            )
            lines.append(
                {
                    "type": "user",
                    "uuid": str(uuid.uuid4()),
                    "timestamp": stamp(),
                    "isSidechain": False,
                    "sessionId": session_id,
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": tu_id, "content": result}
                        ],
                    },
                }
            )
        if last_turn and working:
            content: list[dict[str, Any]] = [
                {
                    "type": "tool_use",
                    "id": f"tu-{uuid.uuid4().hex[:8]}",
                    "name": "Bash",
                    "input": {},
                }
            ]
            stop = "tool_use"
        else:
            content = [{"type": "text", "text": reply}]
            stop = "end_turn"
        lines.append(
            {
                "type": "assistant",
                "uuid": str(uuid.uuid4()),
                "requestId": f"req-{uuid.uuid4().hex[:8]}",
                "timestamp": stamp(),
                "isSidechain": False,
                "sessionId": session_id,
                "message": {
                    "id": f"msg-{uuid.uuid4().hex[:8]}",
                    "role": "assistant",
                    "model": _DEMO_MODEL,
                    "stop_reason": stop,
                    "usage": {
                        "input_tokens": 35,
                        "cache_read_input_tokens": in_tok,
                        "output_tokens": 240,
                    },
                    "content": content,
                },
            }
        )

    (folder / f"{session_id}.jsonl").write_text(
        "\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8"
    )


# Per-workspace transcript scripts. Each entry: ai-title plus the
# (human, assistant, [(tool, result)]) turn triples. Kept realistic and
# generic — no host-specific names or paths.
_TRANSCRIPTS: dict[str, tuple[str, list[tuple[str, str, list[tuple[str, str]]]]]] = {
    "auth-refactor": (
        "Move auth to request-scope tokens",
        [
            (
                "Move token reading off the cookie jar and onto the request scope.",
                "Pulled token resolution into the middleware; gated the legacy header path.",
                [("Read", "middleware.py"), ("Edit", "+47 / -12")],
            ),
            (
                "Now run the auth tests and fix anything that breaks.",
                "Running the auth suite.",
                [("Bash", "pytest tests/auth -q")],
            ),
        ],
    ),
    "docs-rewrite": (
        "Rewrite the docs landing page",
        [
            (
                "Rebuild the docs and confirm there are no strict warnings.",
                "Build is clean: 32 pages, 0 warnings. The landing page leads with one hero shot.",
                [("Bash", "mkdocs build --strict"), ("Edit", "index.md")],
            ),
        ],
    ),
    "perf-bench": (
        "Benchmark list and peek",
        [
            (
                "Benchmark the list and peek paths against the current branch.",
                "List is 30% faster and peek 32% faster than baseline, stable across five repeats.",
                [("Bash", "python -m grove.tools.bench --repeat 5")],
            ),
        ],
    ),
    "nav-redesign": (
        "Collapse the primary nav",
        [
            (
                "Collapse the nav into a single sticky row with a repo dropdown.",
                "Done. One sticky line on narrow viewports; active state reads from the router.",
                [("Edit", "+88 / -54")],
            ),
        ],
    ),
    "mobile-fixes": (
        "Fix the mobile card grid",
        [
            (
                "Make the card grid reflow to one column on phones and respect the safe area.",
                "Reflowing below 640px now.",
                [("Edit", "app-shell.tsx"), ("Bash", "npm test -- src/layout")],
            ),
        ],
    ),
}


@dataclass(slots=True)
class Fleet:
    """The seeded demo fleet handed back to a capture tool."""

    primary: WorkspaceManager  # acme-api — the repo the TUI opens on
    secondary: WorkspaceManager  # acme-web
    store: JsonWorkspaceStore

    def managers(self) -> tuple[WorkspaceManager, WorkspaceManager]:
        return self.primary, self.secondary


def seed_fleet(work: Path, store: JsonWorkspaceStore) -> Fleet:
    """Build the canonical demo fleet across two repos sharing one store.

    ``acme-api`` is the primary repo the TUI opens on. Its top (most recent)
    workspace is a live Claude session mid-task, so the peek rail, the steer
    modal, and the sessions browser all render against real content.
    ``acme-web`` gives the project switcher and the dashboard a second repo
    to show. One ``acme-api`` workspace is taken OFFLINE by killing its tmux
    session, so the status palette is visible at a glance.
    """
    api_repo = make_repo(work, "acme-api")
    web_repo = make_repo(work, "acme-web")
    api = _make_manager(api_repo, store)
    web = _make_manager(web_repo, store)

    # (manager, title, agent, branch, working) — created bottom-up because
    # `list()` returns newest-first, so the last row created floats to the
    # top and becomes the default selection.
    plan: list[tuple[WorkspaceManager, str, str, str, bool]] = [
        (web, "nav-redesign", "claude", "feat/nav-redesign", False),
        (web, "mobile-fixes", "claude", "fix/mobile-layout", True),
        (api, "perf-bench", "claude", "perf/bench", False),
        (api, "flaky-test-fix", "aider", "fix/flaky-tests", False),
        (api, "docs-rewrite", "claude", "docs/rewrite", False),
        (api, "auth-refactor", "claude", "feat/auth-refactor", True),
    ]
    created: list[tuple[WorkspaceState, bool]] = []
    for manager, title, agent, branch, working in plan:
        state = manager.create(
            CreateWorkspaceRequest(
                agent_name=agent,
                title=title,
                branch_plan=NewNamedBranch(name=branch),
            )
        )
        created.append((state, working))

    # Make the git summary non-trivial: the live sessions are dirty, and a
    # couple carry a commit ahead of the base branch.
    for state, working in created:
        worktree = Path(state.worktree_path)
        if working:
            _dirty(worktree)
        if state.title in {"auth-refactor", "docs-rewrite"}:
            _ahead(worktree, state.title)

    # Plant transcripts for every Claude session so the activity line,
    # transcript tab, chat panel, and sessions browser read real turns.
    for state, working in created:
        if state.agent_kind == "claude_code" and state.agent_session_id:
            script = _TRANSCRIPTS.get(state.title)
            if script is not None:
                ai_title, turns = script
                _plant_transcript(
                    worktree=Path(state.worktree_path),
                    session_id=state.agent_session_id,
                    ai_title=ai_title,
                    turns=turns,
                    working=working,
                )

    # Let the stub agents start, print, and reach `sleep` so capture-pane
    # returns the printed content rather than an empty pane.
    time.sleep(3)

    # Take `perf-bench` OFFLINE by killing its tmux session out from under
    # the reconciler, so the list shows an offline row + the respawn key.
    offline = next(s for s, _ in created if s.title == "perf-bench")
    tmux_mod.kill_session(offline.tmux_session)
    return Fleet(primary=api, secondary=web, store=store)


def teardown(*managers: WorkspaceManager) -> None:
    """Kill every tmux session these managers own, ignoring failures."""
    seen: set[str] = set()
    for manager in managers:
        for state in manager.list():
            if state.tmux_session in seen:
                continue
            seen.add(state.tmux_session)
            try:
                if tmux_mod.has_session(state.tmux_session):
                    tmux_mod.kill_session(state.tmux_session)
            except Exception:
                pass
