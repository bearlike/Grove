"""What one create will launch, decided once for the preview, the defaults view and the create.

A create request may leave any launch field unset, and every client used to
fill the gaps itself: the TUI from its form, the CLI from ``QuickCreate``, the
web composer from ``GET /defaults``. Each copy drifted from the engine in a
different direction, and the worst of them displayed one branch choice while
the create made another. :class:`LaunchResolver` is the one answer. It is PURE
over a resolved :class:`GroveConfig` and a request: no git, no Docker, no
filesystem, so a preview can call it per keystroke and a create can call it
first, before any side effect. Probing (container preflight, branch existence)
stays with the callers that own that I/O.

Precedence per field is the request, then the project's saved defaults, then
the user's, then a built-in. The built-ins are deliberately the least
surprising launch: the agent on the host, in the current checkout, with no new
branch or worktree. Configuring a runtime only makes it AVAILABLE; it never
selects it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from grove.core.config import AgentKind, AgentSpec, BranchMode, GroveConfig, LaunchSource
from grove.core.contracts.branch_plan import AutoBranch, BranchPlan, RootBranch
from grove.core.contracts.requests import CreateWorkspaceRequest
from grove.core.errors import InvalidLaunch
from grove.core.workspace import Runtime


@dataclass(frozen=True, slots=True)
class LaunchProblem:
    """One reason the create would be refused, named by the request field that owns it.

    ``field`` is a ``CreateWorkspaceRequest`` field name so a client can put the
    refusal beside the control that sets it; ``error`` is the stable code the
    daemon also answers a refused create with.
    """

    field: str
    error: str
    message: str


@dataclass(frozen=True, slots=True)
class LaunchAdjustment:
    """One answer the engine changed because the draft as written could not run.

    ``field`` is the ``CreateWorkspaceRequest`` field whose answer moved,
    ``value`` the answer it took, and ``reason`` one short sentence a form
    shows as-is. Only answers the person did NOT choose are adjusted; an
    explicit choice that cannot run stays a ``LaunchProblem``.
    """

    field: str
    value: str
    reason: str


@dataclass(frozen=True, slots=True)
class LaunchResolution:
    """The effective launch choices for one request, each with where it came from.

    A ``None`` in ``agent`` or ``branch_plan`` means the choice could not be
    made, and ``problems`` says why. ``sources`` is keyed by request field name.
    """

    agent: AgentSpec | None
    runtime: Runtime
    branch_plan: BranchPlan | None
    model: str | None
    brief: bool
    skip_init: bool
    sources: dict[str, LaunchSource] = field(default_factory=dict)
    problems: tuple[LaunchProblem, ...] = ()
    adjustments: tuple[LaunchAdjustment, ...] = ()

    def raise_first(self) -> None:
        """Refuse the create with the first problem, before any side effect."""
        if self.problems:
            first = self.problems[0]
            raise InvalidLaunch(first.message, field=first.field, error=first.error)


@dataclass(frozen=True, slots=True)
class ProfileResolution:
    """The profile selected for a known runtime without performing admission."""

    agent: AgentSpec | None
    source: LaunchSource
    adjustment: LaunchAdjustment | None = None


class LaunchResolver:
    """Resolve a create request's unset launch fields against one repo's config."""

    #: Saved branch modes that need a concrete name. ``WorkspaceDefaults`` stores
    #: no branch or remote name, so such a default cannot become a plan on its
    #: own, and quietly creating a fresh branch instead would change its meaning.
    _NAMED_MODES: ClassVar[frozenset[str]] = frozenset({"new", "existing", "remote"})
    #: The branch mode a create gets with nothing requested and nothing saved:
    #: the current checkout. One named value so the built-in is stated once.
    BUILTIN_BRANCH_MODE: ClassVar[BranchMode] = "root"

    @classmethod
    def resolve(  # noqa: PLR0912
        cls, cfg: GroveConfig, request: CreateWorkspaceRequest
    ) -> LaunchResolution:
        """The launch ``request`` will get under ``cfg``; never raises."""
        sources: dict[str, LaunchSource] = {}
        problems: list[LaunchProblem] = []

        runtime = cls._runtime(cfg, request, sources)
        profile = cls.profile_for(cfg, request, runtime, request.remote_runtime)
        agent = cls._agent(profile, request, sources, problems)
        plan = cls._branch_plan(cfg, request, sources, problems)
        adjustments: list[LaunchAdjustment] = []
        if profile.adjustment is not None:
            adjustments.append(profile.adjustment)
        plan, remote_problem, remote_adjustment = cls._remote_branch_plan(
            cfg, request, runtime, plan
        )
        if remote_problem is not None:
            problems.append(remote_problem)
        if remote_adjustment is not None:
            adjustments.append(remote_adjustment)

        if request.model is not None:
            model: str | None = request.model
            sources["model"] = "request"
        else:
            model = cfg.default_model_for(agent.name) if agent is not None else None
            sources["model"] = cfg.default_source("model") if model is not None else "builtin"

        if request.brief is not None:
            brief = request.brief
            sources["brief"] = "request"
        elif cfg.defaults.brief is not None:
            brief = cfg.defaults.brief
            sources["brief"] = cfg.default_source("brief")
        else:
            brief = cfg.brief.enabled
            sources["brief"] = "config"

        if request.skip_init is not None:
            skip_init = request.skip_init
            sources["skip_init"] = "request"
        elif cfg.defaults.skip_init is not None:
            skip_init = cfg.defaults.skip_init
            sources["skip_init"] = cfg.default_source("skip_init")
        else:
            skip_init = False
            sources["skip_init"] = "builtin"

        if request.project_cwd is not None:
            sources["project_cwd"] = "request"
        else:
            sources["project_cwd"] = "config" if cfg.agent_cwds.default_path() else "builtin"

        return LaunchResolution(
            agent=agent,
            runtime=runtime,
            branch_plan=plan,
            model=model,
            brief=brief,
            skip_init=skip_init,
            sources=sources,
            problems=tuple(problems),
            adjustments=tuple(adjustments),
        )

    @staticmethod
    def _remote_branch_plan(
        cfg: GroveConfig,
        request: CreateWorkspaceRequest,
        runtime: Runtime,
        plan: BranchPlan | None,
    ) -> tuple[BranchPlan | None, LaunchProblem | None, LaunchAdjustment | None]:
        """Turn an inherited root plan into a remote-safe branch, never an explicit one."""
        if runtime is not Runtime.REMOTE or not isinstance(plan, RootBranch):
            return plan, None, None
        if request.branch_plan is not None:
            return (
                plan,
                LaunchProblem(
                    field="branch_plan",
                    error="remote_requires_worktree",
                    message=(
                        "a remote workspace runs on its own clone and pushes its branch, so it "
                        "needs an isolated branch rather than the current checkout"
                    ),
                ),
                None,
            )
        return (
            AutoBranch(base_ref=cfg.defaults.base_ref or "HEAD"),
            None,
            LaunchAdjustment(
                field="branch_plan",
                value="auto",
                reason="A remote runtime works on its own branch.",
            ),
        )

    @staticmethod
    def _agent(
        profile: ProfileResolution,
        request: CreateWorkspaceRequest,
        sources: dict[str, LaunchSource],
        problems: list[LaunchProblem],
    ) -> AgentSpec | None:
        sources["agent_name"] = profile.source
        if profile.agent is None:
            if request.agent_name is not None:
                problems.append(
                    LaunchProblem(
                        "agent_name",
                        "unknown_agent",
                        f"unknown agent: {request.agent_name}",
                    )
                )
            else:
                problems.append(LaunchProblem("agent_name", "no_agent", "no agents are configured"))
        return profile.agent

    @classmethod
    def profile_for(
        cls,
        cfg: GroveConfig,
        request: CreateWorkspaceRequest,
        runtime: Runtime,
        remote_runtime: str | None = None,
    ) -> ProfileResolution:
        """Select one tool profile after the runtime is known, without I/O.

        The manager calls this again after a container fallback, so the launch's
        recorded runtime and profile are always selected from the same row.
        """
        if request.agent_name is not None:
            return ProfileResolution(cfg.find_agent(request.agent_name), "request")
        tool = cls._tool(cfg, request)
        if tool is None:
            return ProfileResolution(None, "config")
        agnostic = cls._runtime_agnostic_profile(cfg, tool)
        profiles = cfg.defaults.profiles or {}
        rows = [runtime.value]
        if runtime is Runtime.REMOTE and remote_runtime is not None:
            rows.insert(0, remote_runtime)
        for row in rows:
            name = profiles.get(row, {}).get(tool)
            if name is None:
                continue
            agent = cfg.find_agent(name)
            adjustment = None
            profile_changed = (
                agent is not None
                and agnostic.agent is not None
                and agent.name != agnostic.agent.name
            )
            if profile_changed:
                assert agent is not None
                label = (
                    remote_runtime if row == remote_runtime and remote_runtime is not None else row
                )
                adjustment = LaunchAdjustment(
                    field="agent_name",
                    value=agent.name,
                    reason=f"Uses {agent.name} on {label}.",
                )
            return ProfileResolution(agent, cfg.profile_source(row, tool), adjustment)
        return agnostic

    @staticmethod
    def _tool(cfg: GroveConfig, request: CreateWorkspaceRequest) -> AgentKind | None:
        if request.agent_kind is not None:
            return request.agent_kind
        if cfg.defaults.agent is not None:
            agent = cfg.find_agent(cfg.defaults.agent)
            if agent is not None:
                return agent.kind
        first = next(iter(cfg.agents), None)
        return first.kind if first is not None else None

    @staticmethod
    def _runtime_agnostic_profile(cfg: GroveConfig, tool: AgentKind) -> ProfileResolution:
        profiles = cfg.defaults.profiles or {}
        wildcard = profiles.get("*", {}).get(tool)
        default = cfg.find_agent(cfg.defaults.agent) if cfg.defaults.agent is not None else None
        default_matches = default if default is not None and default.kind == tool else None
        if wildcard is not None and (
            default_matches is None or cfg.wildcard_profile_precedes_default_agent(tool)
        ):
            return ProfileResolution(cfg.find_agent(wildcard), cfg.profile_source("*", tool))
        if default_matches is not None:
            return ProfileResolution(default_matches, cfg.default_source("agent"))
        first = next((agent for agent in cfg.agents if agent.kind == tool), None)
        return ProfileResolution(first, "config")

    @classmethod
    def _runtime(
        cls, cfg: GroveConfig, request: CreateWorkspaceRequest, sources: dict[str, LaunchSource]
    ) -> Runtime:
        if request.runtime is not None:
            sources["runtime"] = "request"
            return request.runtime
        if cfg.defaults.runtime is not None:
            sources["runtime"] = cfg.default_source("runtime")
            return Runtime(cfg.default_runtime)
        profile_row = cls.profile_runtime_row(cfg, request)
        if profile_row is not None:
            sources["runtime"] = "config"
            return profile_row[0]
        sources["runtime"] = "builtin"
        return Runtime(cfg.default_runtime)

    @classmethod
    def profile_runtime_row(
        cls, cfg: GroveConfig, request: CreateWorkspaceRequest
    ) -> tuple[Runtime, str | None] | None:
        """The sole concrete profile row that can infer an omitted runtime."""
        if (
            request.agent_name is not None
            or request.runtime is not None
            or cfg.defaults.runtime is not None
        ):
            return None
        tool = cls._tool(cfg, request)
        if tool is None:
            return None
        profile = cls._runtime_agnostic_profile(cfg, tool).agent
        if profile is None:
            return None
        rows: list[tuple[Runtime, str | None]] = []
        profiles = cfg.defaults.profiles or {}
        for row in ("host", "container"):
            if profiles.get(row, {}).get(tool) == profile.name:
                rows.append((Runtime(row), None))
        for row in cfg.resident_runtimes:
            if profiles.get(row, {}).get(tool) == profile.name:
                rows.append((Runtime.REMOTE, row))
        return rows[0] if len(rows) == 1 else None

    @classmethod
    def _branch_plan(
        cls,
        cfg: GroveConfig,
        request: CreateWorkspaceRequest,
        sources: dict[str, LaunchSource],
        problems: list[LaunchProblem],
    ) -> BranchPlan | None:
        if request.branch_plan is not None:
            sources["branch_plan"] = "request"
            return request.branch_plan
        mode = cfg.defaults.branch_mode
        if mode is None:
            sources["branch_plan"] = "builtin"
            mode = cls.BUILTIN_BRANCH_MODE
        else:
            sources["branch_plan"] = cfg.default_source("branch_mode")
        if mode == "root":
            return RootBranch()
        if mode in cls._NAMED_MODES:
            problems.append(
                LaunchProblem(
                    field="branch_plan",
                    error="incomplete_default",
                    message=(
                        f"the saved branch mode {mode!r} needs a branch name, which a saved "
                        "default does not hold; choose the branch for this create"
                    ),
                )
            )
            return None
        return AutoBranch(base_ref=cfg.defaults.base_ref or "HEAD")


__all__ = ["LaunchAdjustment", "LaunchProblem", "LaunchResolution", "LaunchResolver"]
