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
"""

from __future__ import annotations

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
from grove.core.sessions import SessionExplorer, SessionListing
from grove.core.tmux import AttachInstruction
from grove.core.workspace import (
    BranchProvenance,
    CommitSummary,
    InitStatus,
    Placement,
    WorkspacePeek,
    WorkspaceState,
    WorkspaceStatus,
)

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
    "RepoRegistry",
    "RootBranch",
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
