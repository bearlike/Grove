# grove.client — transport-agnostic attach (local PTY / SSH)

> ↑ [root](../../../CLAUDE.md)

Attach a user to a workspace's tmux session, and talk to the daemon, over either a local connection or SSH — the same client code regardless of transport.

## Attach

- **Two `AttachSession` impls share one Protocol so the terminal bridge is transport-agnostic.** `LocalAttach` uses stdlib `pty.fork` + `asyncio.add_reader` for local backends: one PTY pair per attach, lifecycle on close is SIGHUP → waitpid → close. `SshAttach` uses `asyncssh.create_process` over the existing `SSHClientConnection` for remote backends, with binary I/O (`encoding=None`). A caller picks the impl by backend and never branches on transport again.
- **Once attached, tmux owns the UI — never reimplement a multiplexer.** Ctrl-b w / Ctrl-b s / copy mode / the status line are tmux's. The client's job ends at wiring stdin/stdout to the session.
- **Production runs `tmux attach -t <session>`; tests substitute via `_command_override`.** The kwarg is keyword-only and underscore-prefixed so it stays a test seam; tests pass deterministic commands like `cat` or `echo` instead of a live tmux.

## Transport

- **`SshTransport` reuses ONE `asyncssh.SSHClientConnection` for both jobs.** The HTTP forward to the daemon (`forward_local_port`) AND the interactive `tmux attach` (`SSHClientProcess`) ride the same TCP session. One connection, two jobs — daemon RPC and terminal attach never open a second SSH session.
- The daemon binds loopback only; remote access is this SSH port-forward. The bind/auth-deferral rationale lives in [daemon](../daemon/CLAUDE.md).

## Cross-platform

- **POSIX-only stdlib (`pty` / `fcntl` / `termios`) MUST be guarded with `if sys.platform != "win32":`.** A bare top-level import raises `ModuleNotFoundError` on Windows during *test collection*, failing every test that transitively imports the module — green on Linux/macOS, red across the board on Windows with no useful context. Guard the import, then raise `NotImplementedError("requires POSIX (use WSL on Windows)")` at the first runtime entry point. The module loads cleanly everywhere; the unsupported path fails loudly only when exercised. Degrade to a typed error, never an import failure (same rule as tmux-needs-WSL2). `tests/client/test_local_attach.py` carries `pytestmark = skipif(win32)` so the suite stays runnable on Windows.

## Session lessons

Add client-specific transport/attach lessons here. Cross-cutting wire-contract facts belong in [root](../../../CLAUDE.md); daemon HTTP/auth facts in [daemon](../daemon/CLAUDE.md).
