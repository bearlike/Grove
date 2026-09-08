"""The two tmux side effects a capture run needs, and nothing else."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from grove.core import tmux as tmux_mod
from grove.core.errors import TmuxError
from grove.core.manager import WorkspaceManager
from grove.core.store import JsonWorkspaceStore


class DemoTmux:
    """Quiet panes on the way in, and no stray sessions on the way out."""

    @staticmethod
    def install_quiet_sessions() -> None:
        """Force demo panes to spawn a bare shell so no MOTD leaks into captures.

        Real `grove` invokes ``$SHELL`` for each new window. On a host with a
        chatty profile (a server MOTD, a system banner), ``capture-pane``
        returns that instead of the agent stub's content. Spawning ``bash
        --noprofile --norc`` keeps the pane clean. Only relevant inside a
        screenshot run, which is why it is a monkeypatch here rather than a
        config knob in the engine.
        """

        # MIRROR THE ENGINE'S SIGNATURE, INCLUDING `size`. The manager passes
        # `cfg.tmux.detached_size` on every create, so a double that omits the
        # keyword fails the first seeded workspace with a TypeError dressed as
        # "failed to set up tmux session" — which is what took the whole
        # pipeline down when the engine gained the argument. The geometry is
        # honoured too, since the peek rail reads a detached pane and 80x24
        # would wrap the stub agent's output differently from a real run.
        def _quiet_create_session(
            name: str, cwd: Path, *, history_limit: int = 50_000, size: str = ""
        ) -> None:
            server = tmux_mod._server()
            if tmux_mod.has_session(name):
                raise TmuxError(f"tmux session already exists: {name}")
            geometry = tmux_mod.parse_size(size)
            try:
                session = server.new_session(
                    session_name=name,
                    start_directory=str(cwd),
                    attach=False,
                    window_command="bash --noprofile --norc",
                    x=geometry[0] if geometry else None,
                    y=geometry[1] if geometry else None,
                )
            except Exception as exc:
                raise TmuxError(f"failed to create tmux session {name}: {exc}") from exc
            try:
                session.set_option("history-limit", str(history_limit))
                session.set_option("mouse", "on")
            except Exception:
                pass

        tmux_mod.create_session = _quiet_create_session

    @classmethod
    def teardown(cls, *managers: WorkspaceManager) -> None:
        """Kill every tmux session these managers own, ignoring failures."""
        cls.kill_sessions(state.tmux_session for manager in managers for state in manager.list())

    @classmethod
    def teardown_recorded(cls, store: JsonWorkspaceStore) -> int:
        """Kill the sessions a store still NAMES, for a store about to be wiped.

        THE RECORD IS THE ONLY THING THAT KNOWS WHICH SESSIONS ARE OURS, AND A
        RESET DESTROYS IT. Skipping teardown for a reused sandbox is deliberate,
        but the next run that reseeds calls `Sandbox.reset()`, which deletes the
        store naming the previous run's sessions — after that nothing can ever
        identify them again, so a full fleet is stranded on the shared tmux
        server, silently, every time. Draining the store first closes that.

        Driven off persisted records and NEVER off a name pattern: matching on
        the demo's own titles would kill a concurrently running capture's
        sessions, which has already happened on this host. A record can only
        name a session this demo created.
        """
        try:
            states = store.load_all()
        except Exception:  # pragma: no cover - a capture tool, not a gate
            return 0
        return cls.kill_sessions(state.tmux_session for state in states)

    @staticmethod
    def kill_sessions(names: Iterable[str]) -> int:
        """Kill each named session once, ignoring failures; return how many died."""
        killed = 0
        for name in dict.fromkeys(names):
            try:
                if tmux_mod.has_session(name):
                    tmux_mod.kill_session(name)
                    killed += 1
            except Exception:
                pass
        return killed
