"""User-configured backend description.

A ``BackendConfig`` is the only thing the user sets. ``GroveClient``
consumes it and picks the right ``Transport``: ``LocalTransport`` when
``ssh_target`` is ``None``, ``SshTransport`` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class BackendConfig:
    """Backend identity + transport selection."""

    label: str
    """Human-readable name shown in a client's backend switcher."""

    ssh_target: str | None = None
    """OpenSSH-syntax target (``user@host`` or any ssh_config alias).
    ``None`` means a local backend — the client spawns its own daemon."""

    daemon_port: int = 7421
    """Daemon port on the remote host. Ignored for local backends
    (LocalTransport always picks an ephemeral port)."""

    daemon_token: str | None = None
    """Bearer token issued by the remote daemon's pairing flow.

    Local backends still use it whenever the client talks HTTP to the daemon
    (``LocalTransport`` does — the spawned daemon now requires auth on every
    endpoint). ``None`` until the first pair, set by the client's pairing
    flow once the user approves on the host. Stored alongside the rest of
    the backend record in ``backends.json``; defense in depth (file lives in
    the user's config dir, mode 600 on Unix)."""
