"""Write the config file a Grove-owned native worker reads at startup."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from grove.core import paths
from grove.core.auth import SessionStore
from grove.core.config import AgentSpec
from grove.core.errors import GroveError
from grove.core.native_worker import NativeWorkerConfig
from grove.core.runtimes import WORKER_CONFIG_ENV, WORKER_TOKEN_ENV, WorkspaceSpec


def worker_label(workspace_id: str) -> str:
    """The session label a workspace's container worker credential carries.

    One label per workspace, so a relaunch's mint retires the previous launch's
    idle credential and `kill` can revoke whatever is left by name.
    """
    return f"worker-{workspace_id}"


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
        resume: bool = False,
        config_root: Path | None = None,
        container_config_root: str | None = None,
        ask_spool_dir: Path | None = None,
        timeout_seconds: float = 180,
        mailbox_url: str = "",
    ) -> NativeLaunch:
        """Compose the worker's argv and drop its config file.

        A host worker reaches the daemon over the same loopback rendezvous every
        other local Grove process uses and mints its own bearer off `auth.json`.
        A container worker cannot read that file, so it is handed a credential
        minted here for this one workspace (`worker_label`), in the same 0600
        file the container already has bind-mounted, and reaches the daemon at
        `mailbox_url` or through the private socket.
        """
        if provider not in AgentSpec.NATIVE_KINDS:
            raise GroveError("native worker supports Claude Code, Codex and OpenCode only")
        daemon_socket = os.environ.get("GROVE_MAILBOX_SOCKET")
        daemon_url = mailbox_url or os.environ.get("GROVE_MAILBOX_URL", "http://127.0.0.1:7421")
        if container and (
            not (mailbox_url or daemon_socket) or not container_config_root or config_root is None
        ):
            raise GroveError(
                "a container's native worker needs a reachable mailbox URL "
                "(daemon.mailbox_url) or a private daemon socket mount, Grove "
                "installed in the image and a reachable private agent config root"
            )
        token = SessionStore().mint_local(worker_label(workspace_id)) if container else None
        root = config_root if container else paths.native_worker_config_path(workspace_id).parent
        assert root is not None
        file_name = f"{workspace_id}.json"
        config = NativeWorkerConfig(
            workspace_id=workspace_id,
            provider=provider,
            command=list(command),
            initial_prompt=initial_prompt,
            resume=resume,
            daemon_url=daemon_url,
            # The URL wins over the socket: a socket path means nothing to a
            # runtime that is not on this host.
            daemon_socket=None if container and mailbox_url else daemon_socket,
            daemon_token=token,
            ask_spool_dir=str(ask_spool_dir) if ask_spool_dir is not None else None,
            timeout_seconds=timeout_seconds,
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

    @staticmethod
    def remote_spec(
        *,
        workspace_id: str,
        image: str,
        provider: str,
        command: tuple[str, ...],
        initial_prompt: str,
        mailbox_url: str,
        repo_url: str,
        branch: str,
        start_commit: str | None,
        timeout_seconds: float,
        resume: bool = False,
        agent_env: Mapping[str, str] | None = None,
        agent_secret_env: Mapping[str, str] | None = None,
    ) -> WorkspaceSpec:
        """The workload a REMOTE workspace's worker boots from.

        A serverless workload has no file Grove could write, so the worker's
        whole config rides ``env`` and its per-workspace bearer (`worker_label`,
        revoked by `kill`) rides ``secret_env``, which no spec, log or record
        ever carries. The worker clones ``repo_url`` and checks out ``branch``
        itself, so nothing dials into the workload. ``resume`` continues the
        session the decoration names, for a workload restored from a snapshot
        whose home still holds the transcript. ``agent_env`` and
        ``agent_secret_env`` are the agent's own launch environment, split by
        the caller into config literals and resolved values; the worker's
        children inherit both, so the agent finds its provider settings.
        """
        if provider not in AgentSpec.NATIVE_KINDS:
            raise GroveError(f"a remote workspace needs a native agent; {provider!r} is not one")
        config = NativeWorkerConfig(
            workspace_id=workspace_id,
            provider=provider,
            command=list(command),
            initial_prompt=initial_prompt,
            resume=resume,
            daemon_url=mailbox_url,
            timeout_seconds=timeout_seconds,
            repo_url=repo_url,
            branch=branch,
            start_commit=start_commit,
        )
        token = SessionStore().mint_local(worker_label(workspace_id))
        return WorkspaceSpec(
            workspace_id=workspace_id,
            image=image,
            command=("grove-native-worker",),
            env={**(agent_env or {}), WORKER_CONFIG_ENV: config.model_dump_json(exclude_none=True)},
            secret_env={**(agent_secret_env or {}), WORKER_TOKEN_ENV: token},
            reach=(mailbox_url, repo_url),
        )
