"""What the workspace records ENTITLE to exist on this host — pure, no I/O."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from grove.core.tmux import TmuxServer
from grove.core.workspace import WorkspaceState

#: The resource classes this wave reconciles. A new collector adds a member.
ResourceClass = Literal["tmux", "process", "files"]


@dataclass(frozen=True, slots=True, order=True)
class ResourceRef:
    """One thing on the host, named the way its collector can find and remove it.

    Attributes:
        cls: The collector that owns it.
        key: The collector's identity for it: a tmux socket name, a pid
            paired with its start time so a recycled pid is never mistaken
            for it, or a file's absolute path.
        workspace_id: What it serves, when it names it: a workspace id, the
            private tmux socket an observer watches, or the agent session id a
            sidecar names when no workspace is known for it.
    """

    cls: ResourceClass
    key: str
    workspace_id: str = ""


class Ledger:
    """The entitlement side of the reconciliation."""

    @staticmethod
    def expected(states: Iterable[WorkspaceState]) -> frozenset[ResourceRef]:
        """Every resource a workspace record still accounts for.

        Any record that exists entitles its resources, whatever its status: a
        RUNNING record whose server died has nothing to find, and a destroyer
        must never remove something a record still names. Only the private
        tmux server is named here; a process is entitled by the workspace it
        serves (:meth:`candidates`).

        Args:
            states: Every persisted workspace record on the host.

        Returns:
            The tmux servers the records name.
        """
        return frozenset(
            ResourceRef("tmux", state.tmux_socket, state.id)
            for state in states
            if state.tmux_socket
        )

    @classmethod
    def candidates(
        cls, found: Iterable[ResourceRef], states: Iterable[WorkspaceState]
    ) -> frozenset[ResourceRef]:
        """What exists and no record accounts for — the only refs a sweep may touch.

        A tmux server is accounted for by name (:meth:`expected`). A process is
        accounted for by what it serves, because its pid is not something a
        record holds: a worker by its workspace id, an observer by the private
        socket it watches. A process serving nothing is never a candidate,
        since nothing says it is Grove's to remove.

        Args:
            found: What the collectors reported.
            states: Every persisted workspace record on the host.
        """
        records = list(states)
        expected = {ref.key for ref in cls.expected(records) if ref.cls == "tmux"}
        served = expected | {state.id for state in records}
        owners = {state.id for state in records} | {
            state.agent_session_id for state in records if state.agent_session_id
        }
        return frozenset(
            ref
            for ref in found
            if (ref.cls == "tmux" and ref.key not in expected)
            or (ref.cls == "process" and ref.workspace_id and ref.workspace_id not in served)
            or (ref.cls == "files" and ref.workspace_id and ref.workspace_id not in owners)
        )

    @staticmethod
    def owns_socket(name: str) -> bool:
        """Whether a tmux socket name is one Grove mints for a workspace."""
        return name.startswith(TmuxServer.SOCKET_PREFIX)


__all__ = ["Ledger", "ResourceClass", "ResourceRef"]
