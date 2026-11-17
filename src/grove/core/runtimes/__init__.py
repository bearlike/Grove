"""The contract every serverless runtime driver satisfies.

A serverless workspace is RESIDENT: one long-running container that dials the
daemon, like the native worker; nothing dials in. Grove EMITS a deployment spec
the customer's tooling applies, then drives the result with the verbs
``ContainerLifecycle`` has (``up``/``status``/``stop``/``rm``) plus the
``freeze``/``thaw`` pair idle detection needs. A platform is one class
satisfying :class:`ServerlessDriver`, configured from its ``runtimes.<name>``
block.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

from grove.core.errors import ContainerError

#: Set to ``1`` on a workload that resumes a frozen workspace from its pushed
#: branch, so the worker continues the session instead of starting one.
RESUME_ENV: Final = "GROVE_RESUME"
#: The container's daemon credential, always delivered as a platform secret
#: reference, never as a value in a spec.
WORKER_TOKEN_ENV: Final = "GROVE_WORKER_TOKEN"
#: The native worker's whole launch config as JSON, for a workload that has no
#: file Grove could write its ``--config`` into.
WORKER_CONFIG_ENV: Final = "GROVE_WORKER_CONFIG"
#: A token the worker hands to its own git commands so it can push the
#: workspace branch, delivered as a platform secret like ``WORKER_TOKEN_ENV``
#: and never passed on to the agent.
PUSH_TOKEN_ENV: Final = "GROVE_PUSH_TOKEN"


class RuntimeState(StrEnum):
    """What a live read of one remote workspace answered.

    Read on demand and never persisted, like ``ContainerState``: a stored copy
    is stale the moment the platform evicts or scales the workload by itself.
    """

    STARTING = "starting"
    RUNNING = "running"
    FROZEN = "frozen"
    STOPPED = "stopped"
    ABSENT = "absent"


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """Everything a driver needs to bring one workspace's container up.

    ``env`` is plain configuration, never a secret. ``secret_env`` is this one
    workload's own credential (the worker's per-workspace bearer): a driver
    passes it only on a private channel to the platform (an API request body),
    never into :meth:`ServerlessDriver.emit_spec`, an argv or a log, and a
    driver with no private channel ignores it and relies on the platform secret
    its ``*_env`` config names. Kept out of ``repr`` so no log line prints it.
    """

    workspace_id: str
    image: str
    command: Sequence[str]
    env: Mapping[str, str] = field(default_factory=dict)
    secret_env: Mapping[str, str] = field(default_factory=dict, repr=False)
    reach: Sequence[str] = ()
    """URLs the workload must dial (the daemon, the git remote), for a platform
    that denies egress by default."""

    def require_env_within(self, limit_bytes: int, platform: str) -> None:
        """Refuse a spec whose environment the platform would not accept whole.

        Raises:
            ProvisionRefused: The environment is over ``limit_bytes``; never
                truncated, because a cut worker config boots nothing useful.
        """
        size = sum(len(k) + len(v) for k, v in {**self.env, **self.secret_env}.items())
        if size > limit_bytes:
            raise ProvisionRefused(
                f"workspace {self.workspace_id}'s environment is {size} bytes, over "
                f"{platform}'s {limit_bytes}-byte limit"
            )


@dataclass(frozen=True, slots=True)
class RemoteHandle:
    """The durable name of one remote workload, persisted on the workspace record.

    ``runtime`` names the owning ``runtimes.<name>`` block and ``ref`` is the
    platform's own identifier (a job execution, a function ARN). It survives a
    daemon restart and is all ``status``/``stop``/``rm`` ever need.
    """

    runtime: str
    ref: str


class ProvisionRefused(ContainerError):
    """The platform declined to start the workload: quota, capacity or policy.

    A ``ContainerError`` so every existing provision-failure arm handles it
    unchanged. Never retried by Grove: the refusal is the platform's answer, and
    retrying into an exhausted quota only spends the next request's budget.
    """


@runtime_checkable
class ServerlessDriver(Protocol):
    """One platform's mapping of the workspace lifecycle onto its own API."""

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """Render the platform-native deployment document the customer's tooling applies."""
        ...

    def up(self, spec: WorkspaceSpec) -> RemoteHandle:
        """Start the workload for this spec.

        Raises:
            ProvisionRefused: The platform declined to start it.
        """
        ...

    def status(self, handle: RemoteHandle) -> RuntimeState | None:
        """Read the workload's state; ``None`` means the platform could not be read.

        Never a confident answer for "cannot tell".
        """
        ...

    def stop(self, handle: RemoteHandle) -> None:
        """Stop the workload, keeping whatever the platform keeps for a restart."""
        ...

    def rm(self, handle: RemoteHandle) -> None:
        """Remove the workload and every platform resource it owns."""
        ...

    def freeze(self, handle: RemoteHandle) -> None:
        """Stop paying for an idle workload; the caller has pushed its branch first."""
        ...

    def thaw(self, handle: RemoteHandle) -> RemoteHandle:
        """Bring a frozen workload back.

        Returns:
            The handle to use from now on, which differs from ``handle`` on a
            platform whose thaw is a fresh execution.
        """
        ...


@runtime_checkable
class HibernatingDriver(Protocol):
    """A resident platform that can release a frozen workload entirely and restore it later.

    A sibling of :class:`ServerlessDriver`, not a widening of it: a platform
    without it simply stays frozen. Hibernation keeps the workload's filesystem
    somewhere that costs no memory and no local disk, and the handle it returns
    is the only state that survives, so it must name everything :meth:`wake`
    needs except the per-launch spec, which is composed fresh because a
    credential is never persisted.
    """

    def hibernate(self, handle: RemoteHandle) -> RemoteHandle:
        """Snapshot a frozen workload, store the snapshot, and end the workload.

        Returns:
            A handle naming the stored snapshot.

        Raises:
            ContainerError: Any step failed; the workload is left as it was,
                and nothing is ended before the snapshot is stored.
        """
        ...

    def wake(self, handle: RemoteHandle, spec: WorkspaceSpec) -> RemoteHandle:
        """Start a new workload from a hibernated handle's snapshot with ``spec``'s launch.

        Returns:
            The new workload's handle.
        """
        ...

    def forget(self, handle: RemoteHandle) -> None:
        """Delete a hibernated handle's stored snapshot. Missing counts as deleted."""
        ...


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One turn for an EPISODIC workspace: restore, run once, push, exit.

    ``branch`` is where the worker restores the worktree from and pushes back
    to; ``session_id`` is empty for the first turn, after which it continues the
    session the previous result named.
    """

    workspace_id: str
    branch: str
    session_id: str
    prompt: str


@dataclass(frozen=True, slots=True)
class TurnResult:
    """What one completed invocation reports back.

    Only ever decoded from the platform's answer, never synthesised: an
    invocation that was cut short raises instead, because a made-up result
    would read as a turn that finished.
    """

    session_id: str
    head_commit: str
    outcome: str

    @classmethod
    def from_wire(cls, raw: object) -> TurnResult:
        """Decode the worker's JSON answer.

        Raises:
            ContainerError: The answer is not a complete result.
        """
        if not isinstance(raw, dict):
            raise ContainerError(f"the turn answered {type(raw).__name__}, not a result")
        try:
            return cls(
                session_id=str(raw["session_id"]),
                head_commit=str(raw["head_commit"]),
                outcome=str(raw["outcome"]),
            )
        except KeyError as exc:
            raise ContainerError(f"the turn's result has no {exc.args[0]!r}") from exc


@runtime_checkable
class EpisodicDriver(Protocol):
    """One platform that runs a workspace one request at a time.

    A sibling of :class:`ServerlessDriver`, not a widening of it: nothing runs
    between turns, so there is no ``up``/``stop``/``freeze``, and an idle
    episodic workspace costs storage only. No mid-turn steer exists; the only
    input is the next turn.
    """

    def emit_spec(self, spec: WorkspaceSpec) -> str:
        """Render the platform-native deployment document the customer's tooling applies."""
        ...

    def run_turn(self, spec: WorkspaceSpec, turn: TurnRequest) -> TurnResult:
        """Invoke the platform once for this turn and return what it reported.

        Raises:
            ProvisionRefused: The platform throttled or refused the invocation.
            ContainerError: The invocation failed or was cut short.
        """
        ...


__all__ = [
    "PUSH_TOKEN_ENV",
    "RESUME_ENV",
    "WORKER_CONFIG_ENV",
    "WORKER_TOKEN_ENV",
    "EpisodicDriver",
    "HibernatingDriver",
    "ProvisionRefused",
    "RemoteHandle",
    "RuntimeState",
    "ServerlessDriver",
    "TurnRequest",
    "TurnResult",
    "WorkspaceSpec",
]
