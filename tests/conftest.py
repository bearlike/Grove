"""Shared pytest fixtures: tmp state, real git repo, fake tmux."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from grove.core import tmux as tmux_mod
from grove.core.config import GroveConfig
from grove.core.errors import TmuxError
from grove.core.tmux import AttachInstruction

# ─── network side effects neutralized ───────────────────────────────────────


@pytest.fixture(autouse=True)
def _offline_release_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may hit GitHub's releases API (#80).

    Patches the module-level fetch seam so a default ``ReleaseChecker`` (the one
    the daemon/TUI build with no injected ``fetcher``) reports "unknown" instead
    of reaching the network. Tests that want a specific result inject a
    ``fetcher`` directly, which bypasses this seam. Same discipline as the fake
    tmux/git boundaries — real I/O never runs under pytest.
    """
    monkeypatch.setattr(
        "grove.core.release.fetch_latest_release_tag",
        lambda repo, *, installed="": None,
    )


# ─── on-disk paths redirected to a tmpdir ───────────────────────────────────


@pytest.fixture
def tmp_state_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect platformdirs paths so tests don't touch real ~/ ."""
    state = tmp_path / "state"
    config = tmp_path / "config"
    state.mkdir()
    config.mkdir()
    monkeypatch.setattr("grove.core.paths.user_state_path", lambda: state / "state.json")
    monkeypatch.setattr("grove.core.paths.user_config_path", lambda: config / "config.json")
    monkeypatch.setattr("grove.core.paths.user_schema_path", lambda: config / "config.schema.json")
    monkeypatch.setattr("grove.core.paths.user_auth_path", lambda: config / "auth.json")
    monkeypatch.setattr(
        "grove.core.paths.user_webapp_sessions_path",
        lambda: config / "webapp-sessions.json",
    )
    return state


# ─── real git repo in a tmpdir ──────────────────────────────────────────────


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on `main`. Returns the canonical path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@grove.local")
    _git(repo, "config", "user.name", "Grove Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init", "--no-verify")
    return repo.resolve()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


# ─── fake tmux backend (in-memory; same module surface as the real one) ─────


class FakeTmux:
    """In-memory stand-in for grove.core.tmux. Tracks calls for assertions."""

    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.layouts: list[tuple[str, str]] = []  # (session_name, agent_name)
        # session_name → cwd the session was created in, and → worktree the
        # layout windows were rooted in. Lets nested-cwd tests assert the agent
        # session starts in the project subdir while the worktree/branch anchor
        # at the repo root (issue #101).
        self.session_cwds: dict[str, Path] = {}
        self.layout_worktrees: dict[str, Path] = {}
        # (session_name, launch_decoration) — lets correlation tests assert the
        # `--session-id <uuid>` argv the manager threaded in from the adapter.
        self.launch_decorations: list[tuple[str, list[str]]] = []
        # (session_name, env, env_unset) — lets #82 tests assert the manager
        # threads the agent's hermetic launch env through to the layout.
        self.launch_envs: list[tuple[str, dict[str, str], tuple[str, ...]]] = []
        self.init_calls: list[tuple[Path, dict[str, str]]] = []
        self.init_exit_code: int = 0  # tests can override
        self.init_stdout: str = ""  # written to log_path on each run
        self.init_stderr: str = ""
        # Tests set entries to hand specific snapshot text back from peek().
        # Keys are tmux targets (e.g. "session-name:agent").
        self.snapshots: dict[str, str] = {}
        # session → window names, in tmux index order. Mirrors what real
        # tmux would report: create_session adds "0", build_workspace_layout
        # rewrites it to [shell, agent]. Tests can poke this directly to
        # simulate sessions that were reorganized externally.
        self.windows: dict[str, list[str]] = {}
        # tmux pane_activity (seconds-ago). Indexed by target spec
        # ("sess:agent"). Default 0 (just-now activity → ACTIVE) so tests
        # that don't care about the activity dimension keep working. Tests
        # exercising IDLE override per-target with a larger value.
        self.activity_seconds_ago: dict[str, int] = {}
        # Default response for any target without an explicit entry. Set to
        # ``None`` to simulate tmux returning no activity timestamp at all.
        self.default_activity_seconds_ago: int | None = 0
        # (target, text) per send_text call — steering tests assert both the
        # resolved pane target and that refusal paths never inject at all.
        self.sent_texts: list[tuple[str, str]] = []
        # (target, ops) per send_keys call — question-answer tests assert the
        # keystroke op list the Claude adapter built landed at the resolved pane,
        # and that refusal paths never reach the injection seam at all.
        self.sent_keys: list[tuple[str, list[Any]]] = []

    def send_text(self, target: str, text: str) -> None:
        self.sent_texts.append((target, text))

    def send_keys(self, target: str, ops: Any) -> None:
        self.sent_keys.append((target, list(ops)))

    def has_session(self, name: str) -> bool:
        return name in self.sessions

    def create_session(self, name: str, cwd: Path, *, history_limit: int = 50_000) -> None:
        del history_limit
        if name in self.sessions:
            raise TmuxError(f"session already exists: {name}")
        self.session_cwds[name] = cwd
        self.sessions.add(name)
        # Real tmux always creates one initial window. build_workspace_layout
        # below renames it; until then the placeholder mirrors that state.
        self.windows[name] = ["0"]

    def kill_session(self, name: str) -> None:
        self.sessions.discard(name)
        self.windows.pop(name, None)

    def build_workspace_layout(
        self,
        session_name: str,
        *,
        cfg: GroveConfig,
        worktree: Path,
        agent: Any,
        launch_decoration: list[str] | None = None,
    ) -> None:
        self.layout_worktrees[session_name] = worktree
        self.layouts.append((session_name, agent.name))
        self.launch_decorations.append((session_name, list(launch_decoration or [])))
        self.launch_envs.append((session_name, dict(agent.env), tuple(agent.env_unset)))
        # Mirrors real `build_workspace_layout`: rename window 0 → shell,
        # add an `agent` window. Tests that want a session reorganized
        # externally (no `agent`, weirdly named windows, etc.) overwrite
        # this list directly.
        self.windows[session_name] = [
            cfg.tmux.shell_window_name,
            cfg.tmux.agent_window_name,
        ]

    def run_init_script(
        self,
        cfg: Any,
        *,
        worktree: Path,
        repo_root: Path,
        extra_env: dict[str, str] | None = None,
        log_path: Path | None = None,
    ) -> int:
        del cfg, repo_root
        self.init_calls.append((worktree, dict(extra_env or {})))
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"--- stdout ---\n{self.init_stdout}--- stderr ---\n{self.init_stderr}",
                encoding="utf-8",
            )
        return self.init_exit_code

    def capture_pane_snapshot(self, target: str, *, history_lines: int = 500) -> str:
        del history_lines
        return self.snapshots.get(target, "")

    def list_windows(self, session: str) -> list[str]:
        return list(self.windows.get(session, []))

    def pane_activity_seconds_ago(self, target: str) -> int | None:
        if target in self.activity_seconds_ago:
            return self.activity_seconds_ago[target]
        return self.default_activity_seconds_ago

    def attach_instruction(self, session_name: str) -> AttachInstruction:
        return AttachInstruction(tmux_session=session_name, inside_outer_tmux=False)


@pytest.fixture
def fake_tmux(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTmux]:
    """Replace every grove.core.tmux module function with a FakeTmux instance."""
    fake = FakeTmux()
    monkeypatch.setattr(tmux_mod, "has_session", fake.has_session)
    monkeypatch.setattr(tmux_mod, "create_session", fake.create_session)
    monkeypatch.setattr(tmux_mod, "kill_session", fake.kill_session)
    monkeypatch.setattr(tmux_mod, "build_workspace_layout", fake.build_workspace_layout)
    monkeypatch.setattr(tmux_mod, "run_init_script", fake.run_init_script)
    monkeypatch.setattr(tmux_mod, "capture_pane_snapshot", fake.capture_pane_snapshot)
    monkeypatch.setattr(tmux_mod, "send_text", fake.send_text)
    monkeypatch.setattr(tmux_mod, "send_keys", fake.send_keys)
    monkeypatch.setattr(tmux_mod, "list_windows", fake.list_windows)
    monkeypatch.setattr(tmux_mod, "pane_activity_seconds_ago", fake.pane_activity_seconds_ago)
    monkeypatch.setattr(tmux_mod, "attach_instruction", fake.attach_instruction)
    yield fake
