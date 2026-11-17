"""The launch preview: what a create request would launch, before anything is created.

A form posts the same ``CreateWorkspaceRequest`` it would create with, and
gets back the effective choices, where each came from, and every refusal the
create would raise first. The engine computes it with the resolution and the
checks ``create`` runs, so what a form shows is what a create does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from grove.core.config import AgentKind, LaunchSource
from grove.core.contracts.branch_plan import BranchPlan
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.contracts.views import LaunchAdjustmentView, LaunchProblemView

if TYPE_CHECKING:
    from grove.core.launch_options import RuntimeOption
    from grove.core.manager import LaunchPreview


class LaunchPreviewView(BaseModel):
    """The effective launch for one draft request, with sources and problems.

    ``problems`` empty means the create's validation passed, never that it
    will succeed: provisioning, git and the container's own readiness are
    checked again, live, by the create. Container readiness here is a reading
    up to a few seconds old, so a preview never waits on Docker.

    Carries no secret, no environment and no host path: ``agent_cwd`` is
    relative to the repository root.
    """

    model_config = ConfigDict(frozen=True)

    agent_name: str | None
    """The roster entry the create launches; ``None`` when no agent resolves."""
    agent_kind: AgentKind | None
    runtime: Literal["host", "container", "remote"]
    """The runtime the create would record, after any fallback."""
    remote_runtime: str | None
    """The ``runtimes.<name>`` a remote create runs on."""
    runtime_fallback_reason: str | None
    """Set when an inherited container would fall back to the host, and why."""
    native: bool
    """Whether the agent would run as a Grove-owned native session."""
    placement: Literal["root", "worktree"] | None
    """``root`` is the current checkout; ``None`` when no plan resolves."""
    branch_plan: BranchPlan | None
    agent_cwd: str
    """Where the agent starts, relative to the repository root; ``""`` is the root."""
    model: str | None
    effort: str | None
    brief: bool
    skip_init: bool
    sources: dict[str, LaunchSource]
    """Where each answer came from, by request field name."""
    problems: tuple[LaunchProblemView, ...]
    """Each refusal the create would raise first, named by the request field that owns it."""
    adjustments: tuple[LaunchAdjustmentView, ...]
    """Each answer the engine changed so this draft can run (a profile chosen for the
    runtime, a new branch for a remote runtime, a container falling back to the host)."""

    @classmethod
    def from_preview(
        cls, preview: LaunchPreview, request: CreateWorkspaceRequest
    ) -> LaunchPreviewView:
        """Serialize the engine's preview; the plan's placement is read off the plan itself."""
        launch = preview.launch
        plan = launch.branch_plan
        return cls(
            agent_name=launch.agent.name if launch.agent is not None else None,
            agent_kind=launch.agent.kind if launch.agent is not None else None,
            runtime=preview.runtime.value,
            remote_runtime=preview.remote_runtime,
            runtime_fallback_reason=preview.runtime_fallback_reason,
            native=preview.native,
            placement=None if plan is None else ("root" if plan.kind == "root" else "worktree"),
            branch_plan=plan,
            agent_cwd=preview.project_subpath,
            model=launch.model,
            effort=request.effort,
            brief=launch.brief,
            skip_init=launch.skip_init,
            sources=dict(launch.sources),
            problems=tuple(
                LaunchProblemView(field=p.field, error=p.error, message=p.message)
                for p in preview.problems
            ),
            adjustments=tuple(
                LaunchAdjustmentView(field=a.field, value=a.value, reason=a.reason)
                for a in preview.adjustments
            ),
        )


class RuntimeOptionView(BaseModel):
    """One place a workspace could run, as a create form offers it.

    Send ``id`` through ``CreateWorkspaceRequest.runtime_fields``: ``host`` and
    ``container`` are runtimes, any other id is a ``remote_runtime``, and a
    row with a ``node`` also sends that ``node``. An unconfigured driver's id
    is ``driver:<kind>`` and is never sent. ``workspace_target`` false
    (a task-only episodic block) is never selectable. ``status`` is measured
    for host and container and ``unknown`` wherever Grove does not probe, never
    a fabricated ``ready``. ``setup`` says what configuring an unset driver
    takes; it names config keys, never a credential. ``driver_label`` is the
    driver's name as a person reads it (``OpenSandbox``), so a form never has
    to show ``driver``'s config keyword; ``None`` for host, container and a
    node's runtime, whose driver is not known here.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: Literal["host", "container", "remote"]
    driver: str | None
    driver_label: str | None
    node: str | None
    workspace_target: bool
    status: Literal["ready", "unavailable", "not_configured", "unknown"]
    reason: str | None
    setup: str | None

    @classmethod
    def from_option(cls, option: RuntimeOption) -> RuntimeOptionView:
        return cls(
            id=option.id,
            kind=option.kind,
            driver=option.driver,
            driver_label=option.driver_label,
            node=option.node,
            workspace_target=option.workspace_target,
            status=option.status,
            reason=option.reason,
            setup=option.setup,
        )


class LaunchOptionsView(BaseModel):
    """Every runtime a create form may offer: this daemon's, then each reachable node's."""

    model_config = ConfigDict(frozen=True)

    runtimes: tuple[RuntimeOptionView, ...]


__all__ = ["LaunchOptionsView", "LaunchPreviewView", "RuntimeOptionView"]
