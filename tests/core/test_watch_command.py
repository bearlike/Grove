"""The command watcher's process and runtime-boundary guarantees."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from grove.core.container_runtime import ContainerRuntimeState
from grove.core.contracts.watches import CommandPredicate
from grove.core.watches.command import CommandWatcher
from grove.core.workspace import Runtime, WorkspaceState, WorkspaceStatus

WORKSPACE_ID = "a" * 32


def _workspace(tmp_path: Path, *, container: ContainerRuntimeState | None = None) -> WorkspaceState:
    now = datetime.now(UTC)
    return WorkspaceState(
        id=WORKSPACE_ID,
        title="watch command",
        repo_root=str(tmp_path),
        branch="watch-command",
        base_branch="main",
        worktree_path=str(tmp_path),
        tmux_session="watch-command",
        agent_name="claude",
        status=WorkspaceStatus.RUNNING,
        created_at=now,
        updated_at=now,
        runtime=Runtime.CONTAINER if container else Runtime.HOST,
        container=container,
    )


def test_timeout_kills_the_process_group_and_its_child(tmp_path: Path, monkeypatch) -> None:
    """A stopped await is insufficient: the process tree must no longer exist."""
    child_pid = tmp_path / "child.pid"
    child = (
        "import os, time\n"
        f"open({str(child_pid)!r}, 'w').write(str(os.getpid()))\n"
        "while True: time.sleep(1)\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        "while True: time.sleep(1)\n"
    )
    monkeypatch.setattr(CommandWatcher, "TIMEOUT_SECONDS", 0.5)
    watcher = CommandWatcher(lambda _: _workspace(tmp_path))

    outcome = watcher.evaluate(
        CommandPredicate(argv=[sys.executable, "-c", parent], workspace_id=WORKSPACE_ID),
        datetime.now(UTC),
    )

    assert outcome is not None
    assert outcome.ok is False
    assert "timed out" in outcome.summary
    assert child_pid.exists(), "the child never reached its running state"
    pid = int(child_pid.read_text(encoding="utf-8"))

    # A zombie counts as dead, and asserting on /proc's ABSENCE instead is what
    # made this pass locally and fail on CI. Grove kills the grandchild but
    # cannot reap it — it is not Grove's child — so the entry lingers until
    # whoever inherited it reaps, and under a container whose PID 1 does not
    # reap, that is never. The property the watcher actually owes is that the
    # process runs no more code, which is exactly "gone or Z".
    def _dead(target: int) -> bool:
        try:
            stat = Path(f"/proc/{target}/stat").read_text(encoding="utf-8")
        except OSError:
            return True
        return stat.rpartition(")")[2].split()[0] == "Z"

    deadline = time.monotonic() + 5
    while not _dead(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert _dead(pid), "the timed-out command left its child running"


def test_container_workspace_runs_the_predicate_through_docker_exec(
    tmp_path: Path, monkeypatch
) -> None:
    """The host must not answer a question about the agent's container environment."""
    received = tmp_path / "argv.json"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!" + sys.executable + "\n"
        "import json, os, sys\n"
        "with open(os.environ['WATCH_DOCKER_ARGV'], 'w') as output:\n"
        "    json.dump(sys.argv[1:], output)\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("WATCH_DOCKER_ARGV", str(received))
    container = ContainerRuntimeState(container_id="c" * 64, remote_user="vscode")
    watcher = CommandWatcher(lambda _: _workspace(tmp_path, container=container))

    outcome = watcher.evaluate(
        CommandPredicate(argv=["probe", "--check"], workspace_id=WORKSPACE_ID), datetime.now(UTC)
    )

    assert outcome is not None
    assert outcome.ok is True
    assert json.loads(received.read_text(encoding="utf-8")) == [
        "exec",
        "-u",
        "vscode",
        "c" * 64,
        "probe",
        "--check",
    ]


def test_truncated_output_says_so(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(CommandWatcher, "OUTPUT_LIMIT_BYTES", 32)
    watcher = CommandWatcher(lambda _: _workspace(tmp_path))

    outcome = watcher.evaluate(
        CommandPredicate(
            argv=[sys.executable, "-c", "print('x' * 100)"], workspace_id=WORKSPACE_ID
        ),
        datetime.now(UTC),
    )

    assert outcome is not None
    assert "Output truncated" in outcome.summary
    assert "x" * 100 not in outcome.summary


def test_missing_workspace_settles_as_a_failed_outcome() -> None:
    watcher = CommandWatcher(lambda _: None)

    outcome = watcher.evaluate(
        CommandPredicate(argv=["true"], workspace_id=WORKSPACE_ID), datetime.now(UTC)
    )

    assert outcome is not None
    assert outcome.ok is False
    assert "no longer exists" in outcome.summary


def test_non_terminal_exit_keeps_waiting(tmp_path: Path) -> None:
    watcher = CommandWatcher(lambda _: _workspace(tmp_path))

    outcome = watcher.evaluate(
        CommandPredicate(
            argv=[sys.executable, "-c", "raise SystemExit(7)"],
            terminal_exit_codes=[0],
            workspace_id=WORKSPACE_ID,
        ),
        datetime.now(UTC),
    )

    assert outcome is None
