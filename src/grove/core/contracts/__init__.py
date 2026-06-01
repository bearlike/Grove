"""Public contracts — Pydantic models that cross client/engine boundaries.

Re-export every wire-level type from a single import surface so clients
write ``from grove.core.contracts import CreateWorkspaceRequest, ...``
without caring which file each lives in. Internal IR types
(``ResolvedBranch``, ``BranchMode``) are deliberately not re-exported
here — they are engine-only and stay private to ``branch_plan``.

Boundary rule (codified in ``CLAUDE.md``): Pydantic for anything that
crosses a client/server boundary now or could in the future; plain
``@dataclass(slots=True)`` for internal in-process state. This module
holds the former; the latter live in ``grove.core.workspace``.
"""

from __future__ import annotations

from grove.core.contracts.auth import (
    AuthErrorEnvelope,
    PairingChallengeView,
    PairRequest,
    PairResultView,
    SessionView,
)
from grove.core.contracts.branch_info import BranchInfo
from grove.core.contracts.branch_plan import (
    AutoBranch,
    BranchPlan,
    ExistingLocalBranch,
    NewNamedBranch,
    TrackRemoteBranch,
)
from grove.core.contracts.requests import CreateWorkspaceRequest, UpdateWorkspaceRequest
from grove.core.contracts.views import (
    AttachInstructionView,
    CommitSummaryView,
    WorkspacePeekView,
    WorkspaceStateView,
)

__all__ = [
    "AttachInstructionView",
    "AuthErrorEnvelope",
    "AutoBranch",
    "BranchInfo",
    "BranchPlan",
    "CommitSummaryView",
    "CreateWorkspaceRequest",
    "ExistingLocalBranch",
    "NewNamedBranch",
    "PairRequest",
    "PairResultView",
    "PairingChallengeView",
    "SessionView",
    "TrackRemoteBranch",
    "UpdateWorkspaceRequest",
    "WorkspacePeekView",
    "WorkspaceStateView",
]
