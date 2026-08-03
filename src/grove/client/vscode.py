"""VS Code attach — launch an external editor pointed at a running container.

A deliberately small transport: a shell in the container is
already free via ``grove attach``, so this is convenience, not new
infrastructure. It is fire-and-forget, unlike :class:`~grove.client.attach.
AttachSession` — VS Code owns its own window and process lifecycle once
launched, so there is no bidirectional PTY/output stream to bridge here.
Still lives in ``client/`` alongside ``attach.py``/``transport.py`` because
that's what a "target" is in this package: one more way to open a
workspace, chosen by workspace shape (host → absent, container → this) the
same way a caller picks ``LocalAttach`` vs ``SshAttach`` by backend.

The remote authority VS Code's Dev Containers extension understands is
``attached-container+<hex of a compact JSON blob>`` — nothing in it is a
VS Code-side handle, session token, or random id, so Grove can emit a
correct URI and launch the editor without ever running VS Code itself or
needing the extension to have created the container.
"""

from __future__ import annotations

import binascii
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Protocol

from grove.client.errors import TransportError

# In launch preference order: stable build first, Insiders as a fallback for
# users who only have that channel installed. Cursor is deliberately absent —
# the Dev Containers extension it would need is proprietary and not on Open
# VSX, so Cursor/VSCodium are structurally locked out (tracked as the SSH
# transport follow-up, not solved here).
_EDITOR_BINARIES = ("code", "code-insiders")


class ContainerTarget(Protocol):
    """The narrowest slice of container state a VS Code attach needs.

    Structural rather than an import of
    ``grove.core.container_runtime.ContainerRuntimeState``: that module also
    carries `ContainerLifecycle` and its `docker` subprocess boundary, and the
    client SDK has no business pulling engine side effects in to name two
    strings. `ContainerRuntimeState` satisfies this Protocol as it stands, so
    every caller passes the real thing — the Protocol only keeps the dependency
    from flowing the wrong way.
    """

    # Read-only properties, not plain attributes: a plain attribute in a
    # Protocol declares a SETTABLE variable, which a frozen model does not
    # satisfy — and `ContainerRuntimeState` is frozen, deliberately. Attach only
    # ever reads, so the read-only form is both correct and the true contract.
    @property
    def container_id(self) -> str: ...

    @property
    def remote_workspace_folder(self) -> str: ...


def build_vscode_uri(
    container: str,
    remote_workspace_folder: str,
    *,
    context: str | None = None,
) -> str:
    """Build the ``vscode-remote://attached-container+<hex>`` URI.

    Pure — no VS Code process is ever run to compute this. The remote
    authority is JSON (``{"containerName": "/<container>"}``, compact
    separators, hex-encoded utf-8) rather than a bare container name: that's
    what the Dev Containers extension itself emits when it attaches, so this
    survives name-vs-id ambiguity and, with ``context`` set, carries a
    non-default Docker context via ``settings.context`` exactly the way the
    extension's own attach flow does. ``container`` may be a container name
    or id — Docker accepts both when resolving ``/<container>``.
    """
    target: dict[str, object] = {"containerName": f"/{container}"}
    if context is not None:
        target["settings"] = {"context": context}
    payload = json.dumps(target, separators=(",", ":"))
    authority = "attached-container+" + binascii.hexlify(payload.encode()).decode()
    return f"vscode-remote://{authority}{remote_workspace_folder}"


@dataclass(frozen=True, slots=True)
class VolumeMount:
    """A named-volume mount, in the shape ``devcontainer.json``'s ``mounts`` /
    ``docker --mount`` both accept (``type``/``source``/``target``).

    Deliberately NOT ``grove.core.devcontainer.DevcontainerMount``: that type
    is owned by the devcontainer overlay layer, and ``client/`` only ever
    depends on ``grove.core.contracts`` (the wire boundary), never on core
    engine internals — reaching into ``core.devcontainer`` from here would
    cross that boundary for a value the consumer can trivially reshape into
    its own type.
    """

    source: str
    target: str
    type: str = "volume"

    def to_flag(self) -> str:
        """The ``docker --mount`` / CLI ``--mount`` flag string form."""
        return f"type={self.type},source={self.source},target={self.target}"


def vscode_server_mount(volume_name: str, *, remote_home: str = "/root") -> VolumeMount:
    """The per-project named-volume mount for VS Code Server's cache directory.

    A mount CONTRIBUTION, not a wire-up: this returns the mount spec and
    nothing else. The consumer is the overlay/infra layer building each
    workspace's devcontainer mounts — that layer decides WHEN to add this to
    a config and what ``volume_name`` to pass (project-scoped, so a fresh
    worktree in the same repo reuses the volume); it is not wired into
    ``manager.py`` or ``container_infra.py`` here, keeping this module out of
    the engine's mount-wiring internals.

    Why a named volume at all: the VS Code Server download is hundreds of MB
    and is pinned to the connecting client's exact commit, so baking it into
    an image goes stale on the next VS Code update. A volume pays the
    download once per VS Code version per project instead of once per
    workspace, without Grove having to track a version.
    """
    return VolumeMount(source=volume_name, target=f"{remote_home}/.vscode-server")


class VsCodeAttach:
    """Launches VS Code (or Insiders) attached to a workspace's container.

    Fire-and-forget: :meth:`start` spawns ``code --folder-uri <uri>``
    detached and returns immediately — VS Code owns its own window from
    there. Absent for host workspaces by construction: a caller with no
    :class:`ContainerTarget` has nothing to build a URI from and never
    constructs this class (the CLI's ``grove code`` enforces this at the
    call site, not here).
    """

    def __init__(self, target: ContainerTarget, *, context: str | None = None) -> None:
        self._uri = build_vscode_uri(
            target.container_id, target.remote_workspace_folder, context=context
        )

    @property
    def uri(self) -> str:
        return self._uri

    async def start(self) -> None:
        """Launch the resolved editor binary detached from this process.

        Raises :class:`TransportError` with an actionable message when
        neither ``code`` nor ``code-insiders`` is on ``PATH`` — the one
        failure mode a user hits routinely (VS Code installed but its shell
        command never added), so the message names the exact fix rather
        than surfacing a bare ``FileNotFoundError``.
        """
        binary = self._resolve_binary()
        # start_new_session detaches VS Code from this process group so it
        # keeps running after the CLI invocation (or daemon request) exits —
        # the same "launch and let it own its own lifecycle" contract as
        # any other external-editor handoff.
        subprocess.Popen([binary, "--folder-uri", self._uri], start_new_session=True)

    @staticmethod
    def _resolve_binary() -> str:
        for name in _EDITOR_BINARIES:
            found = shutil.which(name)
            if found is not None:
                return found
        raise TransportError(
            "`code` (or `code-insiders`) not found on PATH — install VS Code's "
            "shell command (Command Palette -> \"Shell Command: Install 'code' "
            'command in PATH") to use `grove code`'
        )
