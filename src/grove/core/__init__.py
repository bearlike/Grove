"""Public API for the Grove engine.

The contract clients depend on. Internal modules (``grove.core.config``,
``grove.core.manager``, ``grove.core.git``, …) are renamable as long as
re-exports here keep working.

Two boundary rules govern what shows up in this list:
  * Pydantic types for everything that crosses a client/engine boundary
    — config, request envelopes, response shapes, discriminated unions
    of intent. These come from ``grove.core.contracts``.
  * Plain ``@dataclass(slots=True)`` for in-process state and engine IR.
    These come from ``grove.core.workspace``.

**Re-exports resolve lazily (PEP 562).** Python executes every parent package's
``__init__`` before any submodule import, so an eager re-export list here made
``import grove.core.paths`` — a module needing only ``platformdirs`` — pay for
``manager`` → ``tmux`` → ``libtmux`` and ``mewbo`` → ``httpx`` → ``pydantic``:
1.13s and 60MB to resolve a filesystem path. That cost landed on the hot path
that matters most, the per-event ``grove agent-hook`` fork, where it burned
~1s of CPU per hook event across every session in the fleet. The name →
module map below defers each import to first attribute access, so the contract
is unchanged (``from grove.core import ActivityService`` still works) while a
leaf import stays a leaf import.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Eagerly visible to mypy/IDEs; never executed at runtime. The lazy
    # `__getattr__` below is what actually resolves these at import time.
    from grove.core.activity import ActivityService, DashboardSnapshot
    from grove.core.config import AgentSpec, GroveConfig, load_config
    from grove.core.contracts import (
        AttachInstructionView,
        AutoBranch,
        BranchInfo,
        BranchPlan,
        CommitSummaryView,
        CreateWorkspaceRequest,
        ExistingLocalBranch,
        NewNamedBranch,
        RootBranch,
        TrackRemoteBranch,
        UpdateWorkspaceRequest,
        WorkspacePeekView,
        WorkspaceStateView,
    )
    from grove.core.errors import (
        BranchAlreadyCheckedOut,
        BranchConflict,
        BranchError,
        BranchNotFound,
        GroveError,
    )
    from grove.core.manager import WorkspaceEvent, WorkspaceManager, build
    from grove.core.registry import RepoRegistry
    from grove.core.release import ReleaseChecker, ReleaseStatus
    from grove.core.sessions import SessionExplorer, SessionListing
    from grove.core.tmux import AttachInstruction
    from grove.core.workspace import (
        BranchProvenance,
        CommitSummary,
        InitStatus,
        Placement,
        ProvisionStatus,
        Runtime,
        WorkspacePeek,
        WorkspaceState,
        WorkspaceStatus,
    )

# Exported name → the internal module that defines it. The single source of
# truth for the lazy resolution below; `__all__` is derived from it so the two
# can never drift.
_EXPORTS: dict[str, str] = {
    "ActivityService": "grove.core.activity",
    "DashboardSnapshot": "grove.core.activity",
    "AgentSpec": "grove.core.config",
    "GroveConfig": "grove.core.config",
    "load_config": "grove.core.config",
    "AttachInstructionView": "grove.core.contracts",
    "AutoBranch": "grove.core.contracts",
    "BranchInfo": "grove.core.contracts",
    "BranchPlan": "grove.core.contracts",
    "CommitSummaryView": "grove.core.contracts",
    "CreateWorkspaceRequest": "grove.core.contracts",
    "ExistingLocalBranch": "grove.core.contracts",
    "NewNamedBranch": "grove.core.contracts",
    "RootBranch": "grove.core.contracts",
    "TrackRemoteBranch": "grove.core.contracts",
    "UpdateWorkspaceRequest": "grove.core.contracts",
    "WorkspacePeekView": "grove.core.contracts",
    "WorkspaceStateView": "grove.core.contracts",
    "BranchAlreadyCheckedOut": "grove.core.errors",
    "BranchConflict": "grove.core.errors",
    "BranchError": "grove.core.errors",
    "BranchNotFound": "grove.core.errors",
    "GroveError": "grove.core.errors",
    "WorkspaceEvent": "grove.core.manager",
    "WorkspaceManager": "grove.core.manager",
    "build": "grove.core.manager",
    "RepoRegistry": "grove.core.registry",
    "ReleaseChecker": "grove.core.release",
    "ReleaseStatus": "grove.core.release",
    "SessionExplorer": "grove.core.sessions",
    "SessionListing": "grove.core.sessions",
    "AttachInstruction": "grove.core.tmux",
    "BranchProvenance": "grove.core.workspace",
    "CommitSummary": "grove.core.workspace",
    "InitStatus": "grove.core.workspace",
    "Placement": "grove.core.workspace",
    "ProvisionStatus": "grove.core.workspace",
    "Runtime": "grove.core.workspace",
    "WorkspacePeek": "grove.core.workspace",
    "WorkspaceState": "grove.core.workspace",
    "WorkspaceStatus": "grove.core.workspace",
}

# Spelled as a literal (not `sorted(_EXPORTS)`) because type checkers only
# understand a static `__all__`; the test suite asserts the two agree.
__all__ = [
    "ActivityService",
    "AgentSpec",
    "AttachInstruction",
    "AttachInstructionView",
    "AutoBranch",
    "BranchAlreadyCheckedOut",
    "BranchConflict",
    "BranchError",
    "BranchInfo",
    "BranchNotFound",
    "BranchPlan",
    "BranchProvenance",
    "CommitSummary",
    "CommitSummaryView",
    "CreateWorkspaceRequest",
    "DashboardSnapshot",
    "ExistingLocalBranch",
    "GroveConfig",
    "GroveError",
    "InitStatus",
    "NewNamedBranch",
    "Placement",
    "ProvisionStatus",
    "ReleaseChecker",
    "ReleaseStatus",
    "RepoRegistry",
    "RootBranch",
    "Runtime",
    "SessionExplorer",
    "SessionListing",
    "TrackRemoteBranch",
    "UpdateWorkspaceRequest",
    "WorkspaceEvent",
    "WorkspaceManager",
    "WorkspacePeek",
    "WorkspacePeekView",
    "WorkspaceState",
    "WorkspaceStateView",
    "WorkspaceStatus",
    "build",
    "load_config",
]


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name by importing its owning module on demand."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    # Cache on the package so subsequent lookups skip __getattr__ entirely.
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return __all__
