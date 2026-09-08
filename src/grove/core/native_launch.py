"""Prepare private launch credentials for explicitly owned native workers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from grove.core import paths
from grove.core.auth import SessionStore
from grove.core.contracts.mailboxes import MailboxAddress, MailboxIdentity
from grove.core.errors import GroveError
from grove.core.native_worker import NativeWorkerConfig


@dataclass(frozen=True, slots=True)
class NativeLaunch:
    command: tuple[str, ...]
    decoration: tuple[str, ...]

    @classmethod
    def prepare(
        cls,
        *,
        workspace_id: str,
        provider: str,
        command: tuple[str, ...],
        initial_prompt: str,
        container: bool,
        config_root: Path | None = None,
        container_config_root: str | None = None,
        ask_spool_dir: Path | None = None,
    ) -> NativeLaunch:
        if provider not in {"claude_code", "codex"}:
            raise GroveError("mailbox native worker supports Claude Code and Codex only")
        daemon_socket = os.environ.get("GROVE_MAILBOX_SOCKET")
        daemon_url = os.environ.get("GROVE_MAILBOX_URL", "http://127.0.0.1:7421")
        if container and (not daemon_socket or not container_config_root or config_root is None):
            raise GroveError(
                "container mailboxes require a private mailbox socket mount, "
                "Grove installed in the image and a reachable private agent config root"
            )
        address = MailboxAddress(workspace_id=workspace_id)
        store = SessionStore()
        store.revoke_mailbox_sessions(address)
        identity = MailboxIdentity(address=address, generation=uuid4().hex)
        peer_token, _ = store.issue_mailbox_session(identity, label="native mailbox peer")
        registration_token, _ = store.issue_mailbox_session(
            identity, label="native mailbox owner", registration=True
        )
        root = config_root if container else paths.user_auth_path().parent / "mailbox-workers"
        assert root is not None
        file_name = f"{workspace_id}.json"
        config = NativeWorkerConfig(
            provider=provider,
            command=list(command),
            initial_prompt=initial_prompt,
            registration_token=registration_token,
            peer_token=peer_token,
            daemon_url=daemon_url,
            daemon_socket=daemon_socket,
            ask_spool_dir=str(ask_spool_dir) if ask_spool_dir is not None else None,
        )
        paths.ensure_dir(root)
        paths.write_atomic(root / file_name, config.model_dump_json(), mode=0o600)
        target = f"{container_config_root}/{file_name}" if container else str(root / file_name)
        worker_command = (
            ("grove-native-worker",)
            if container
            else (sys.executable, "-m", "grove.core.native_worker")
        )
        return cls(worker_command, ("--config", target))
