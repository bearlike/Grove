"""Where a workspace may run: every runtime Grove supports, configured or not, and why.

A create form needs more than the runtimes that are configured: it should show
a supported runtime that is not set up, with what setting it up takes, and it
must never offer a task-only (episodic) block as somewhere a workspace can
live. :func:`runtime_options` answers that from one repo's config plus two
readiness readings the caller took. It is pure, so it probes nothing; the
supported driver set is read off the config's own discriminated unions, so a
new driver appears here by being added there.

Readiness is reported, never fabricated: host and container are measured by
the same preflight ``grove doctor`` runs, while a remote block's platform is
not probed at all and so reads ``unknown``.
"""

from __future__ import annotations

import typing
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from grove.core.config import EpisodicRuntimeConfig, GroveConfig, ResidentRuntimeConfig

RuntimeStatus = Literal["ready", "unavailable", "not_configured", "unknown"]


@dataclass(frozen=True, slots=True)
class RuntimeOption:
    """One place a workspace could run, as a create form offers it.

    ``id`` is what a create sends: ``host`` and ``container`` as the runtime,
    any other id as ``remote_runtime`` (``CreateWorkspaceRequest.runtime_fields``),
    always the exact name a create accepts today. An unconfigured driver is not
    selectable, so its id is ``driver:<kind>``: unique even beside a block a
    user happened to name after a driver kind, and never a sendable name.
    """

    id: str
    kind: Literal["host", "container", "remote"]
    driver: str | None
    driver_label: str | None
    node: str | None
    workspace_target: bool
    status: RuntimeStatus
    reason: str | None
    setup: str | None


def _kind(config_class: type) -> str:
    return str(config_class.model_fields["kind"].default)  # type: ignore[attr-defined]


def _label(kind: str) -> str:
    return str(_DRIVERS[kind].LABEL)


def _summary(config_class: type) -> str:
    return (config_class.__doc__ or "").strip().splitlines()[0]


#: Every driver kind, from the config unions themselves.
_RESIDENT = {_kind(c): c for c in typing.get_args(ResidentRuntimeConfig)}
_EPISODIC = {_kind(c): c for c in typing.get_args(EpisodicRuntimeConfig)}
_DRIVERS = {**_RESIDENT, **_EPISODIC}

_EPISODIC_REASON = "runs one task per invocation, so a workspace cannot live on it"


def runtime_options(
    cfg: GroveConfig,
    *,
    host_unavailable: str | None,
    container_unavailable: str | None,
) -> list[RuntimeOption]:
    """This daemon's runtime catalog: host, container, each block, then each unset driver."""
    options = [
        RuntimeOption(
            id="host",
            kind="host",
            driver=None,
            driver_label=None,
            node=None,
            workspace_target=True,
            status="unavailable" if host_unavailable else "ready",
            reason=host_unavailable,
            setup=None,
        ),
        RuntimeOption(
            id="container",
            kind="container",
            driver=None,
            driver_label=None,
            node=None,
            workspace_target=True,
            status="unavailable" if container_unavailable else "ready",
            reason=container_unavailable,
            setup=None,
        ),
    ]
    configured: set[str] = set()
    for name in sorted(cfg.runtimes):
        block = cfg.runtimes[name]
        configured.add(block.kind)
        episodic = block.kind in _EPISODIC
        options.append(
            RuntimeOption(
                id=name,
                kind="remote",
                driver=block.kind,
                driver_label=_label(block.kind),
                node=None,
                workspace_target=not episodic,
                status="unavailable" if episodic else "unknown",
                reason=_EPISODIC_REASON if episodic else "the platform is not probed",
                setup=None,
            )
        )
    for kind, config_class in _DRIVERS.items():
        if kind in configured:
            continue
        episodic = kind in _EPISODIC
        options.append(
            RuntimeOption(
                id=f"driver:{kind}",
                kind="remote",
                driver=kind,
                driver_label=_label(kind),
                node=None,
                workspace_target=not episodic,
                status="not_configured",
                reason=_EPISODIC_REASON if episodic else None,
                setup=(
                    f"{_summary(config_class)} Add a runtimes.<name> block with "
                    f"kind: {kind} to your Grove config."
                ),
            )
        )
    return options


def node_options(node: str, runtimes: Sequence[str]) -> list[RuntimeOption]:
    """A connected node's runtimes, as a hub sees them: names only, readiness unknown.

    A node advertises its runtime NAMES, not their kinds or health, so a hub
    can honestly offer each one by name and say nothing more about it.
    """
    return [
        RuntimeOption(
            id=name,
            kind="host" if name == "host" else "container" if name == "container" else "remote",
            driver=None,
            driver_label=None,
            node=node,
            workspace_target=True,
            status="unknown",
            reason="this node's readiness is not probed from the hub",
            setup=None,
        )
        for name in ("host", "container", *runtimes)
    ]


__all__ = ["RuntimeOption", "RuntimeStatus", "node_options", "runtime_options"]
