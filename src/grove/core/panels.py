"""Resolve one workspace panel to the service IP inside its owned compose stack.

A panel config deliberately contains no host, URL, or Docker identifier.  A
committed project config is input an agent can change, so accepting an arbitrary
origin would make the daemon a host-network proxy.  The only coordinate it may
name is a compose *service*; this resolver accepts that coordinate only after
``ContainerRuntimeState.compose_owned`` has already corroborated the persisted
compose project against the Grove-labelled container that ``devcontainer up``
produced.  It then asks Docker for the one container carrying that project's
service label.

That is intentionally not a second ownership proof.  ``compose_owned`` is the
one proof and is recorded at mint time by ``ContainerRuntimeState.owns_project``.
The compose labels below merely resolve a service *within that already-proved
project*.  A stopped workspace, an absent service, or an unreadable engine is an
unavailable panel rather than an exception on a dashboard render path.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from grove.core.config import PanelConfig
from grove.core.container_runtime import DockerCli
from grove.core.workspace import LIVE_STATUSES, WorkspaceState, WorkspaceStatus

#: Statuses a panel may resolve under.
#:
#: ``LIVE_STATUSES`` alone is WRONG here and silently resolves every panel to
#: ``None``. ACTIVE/IDLE are *derived* views that only ``list()`` and ``peek()``
#: promote a state into; a workspace read through ``manager.get()`` still carries
#: the raw persisted RUNNING intent. This mirrors ``ensure_can_pause``, which
#: spells the same union for the same reason.
_RESOLVABLE_STATUSES = LIVE_STATUSES | {WorkspaceStatus.RUNNING}


@dataclass(frozen=True, slots=True)
class PanelTarget:
    """The internal destination of one currently available panel.

    The IP rather than a compose DNS name is deliberate.  Docker's embedded DNS
    makes a service name convenient but it does not state which compose project
    it belongs to; the Docker label query below does.  The target is ephemeral by
    nature and is resolved for each request, so it never enters the workspace
    record or becomes a stale second source of truth.
    """

    host: str
    port: int


class PanelResolver:
    """Resolve declared panels through the workspace's verified compose stack.

    Docker remains the side-effect boundary through ``DockerCli``.  The resolver
    takes it by injection because panel visibility is a read-path fact: tests can
    script Docker's answers without a daemon, while production gets the ordinary
    bounded subprocess client.  ``None`` is the only unavailable representation;
    callers choose whether that means omission from a listing or a clean refusal
    for a direct request.
    """

    _IPV4_FORMAT = "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"

    def __init__(self, *, docker: DockerCli | None = None) -> None:
        self._docker = docker or DockerCli(timeout=5.0)

    def resolve(self, state: WorkspaceState, panel: PanelConfig) -> PanelTarget | None:
        """Resolve *panel* only if its workspace is a live, owned compose stack.

        ``compose_owned`` is the existing compose ownership assertion, not an
        optional hardening layer: without it a project name read from a persisted
        record would be enough to enumerate somebody else's stack.  No committed
        config-layer stripping is necessary for panels because their service and
        port are contained here by that already-owned project; unlike a command
        or host path, neither expands what the config can reach.
        """
        container = state.container
        if (
            state.status not in _RESOLVABLE_STATUSES
            or container is None
            or not container.is_compose
            or not container.compose_owned
            or not container.compose_project
        ):
            return None

        candidates = (
            self._docker.read(container.compose_service_argv(panel.service)) or ""
        ).split()
        # A scaled compose service has no single stable panel destination.  Picking
        # one would turn refreshes into a silent, accidental load balancer.
        if len(candidates) != 1:
            return None

        raw_ip = self._docker.read(["docker", "inspect", "-f", self._IPV4_FORMAT, candidates[0]])
        ip = (raw_ip or "").strip()
        try:
            parsed = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if parsed.version != 4:
            return None
        return PanelTarget(host=ip, port=panel.port)


__all__ = ["PanelResolver", "PanelTarget"]
