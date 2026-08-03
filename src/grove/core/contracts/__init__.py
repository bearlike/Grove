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

from grove.core.contracts.activity import (
    AgentActivityView,
    AgentSessionView,
    DashboardEvent,
    DashboardSnapshotView,
    ProjectGroupView,
    SessionActivityView,
    WorkspaceActivityView,
)
from grove.core.contracts.agents import AgentSummaryView
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
    RootBranch,
    TrackRemoteBranch,
)
from grove.core.contracts.issueops import (
    IssueOpsAction,
    IssueOpsEvent,
    IssueOpsOutcome,
)
from grove.core.contracts.questions import (
    AgentQuestionOptionView,
    AgentQuestionView,
    QuestionAnswerItem,
    QuestionAnswerRequest,
)
from grove.core.contracts.requests import CreateWorkspaceRequest, UpdateWorkspaceRequest
from grove.core.contracts.sessions import (
    DigestEntryView,
    RemapSessionRequest,
    SessionDetailView,
    SessionSummaryView,
    SessionTurnView,
)
from grove.core.contracts.tickets import (
    TicketProviderName,
    TicketProviderView,
    TicketRef,
    TicketSelector,
)
from grove.core.contracts.views import (
    ATTACH_INSTRUCTION_ADAPTER,
    AttachInstructionView,
    CommitSummaryView,
    ContainerAttachView,
    HostAttachView,
    ProjectView,
    ProvisionProgressView,
    WorkspacePaneView,
    WorkspacePeekView,
    WorkspaceStateView,
    attach_instruction_view,
)

__all__ = [
    "ATTACH_INSTRUCTION_ADAPTER",
    "AgentActivityView",
    "AgentQuestionOptionView",
    "AgentQuestionView",
    "AgentSessionView",
    "AgentSummaryView",
    "AttachInstructionView",
    "AuthErrorEnvelope",
    "AutoBranch",
    "BranchInfo",
    "BranchPlan",
    "CommitSummaryView",
    "ContainerAttachView",
    "CreateWorkspaceRequest",
    "DashboardEvent",
    "DashboardSnapshotView",
    "DigestEntryView",
    "ExistingLocalBranch",
    "HostAttachView",
    "IssueOpsAction",
    "IssueOpsEvent",
    "IssueOpsOutcome",
    "NewNamedBranch",
    "PairRequest",
    "PairResultView",
    "PairingChallengeView",
    "ProjectGroupView",
    "ProjectView",
    "ProvisionProgressView",
    "QuestionAnswerItem",
    "QuestionAnswerRequest",
    "RemapSessionRequest",
    "RootBranch",
    "SessionActivityView",
    "SessionDetailView",
    "SessionSummaryView",
    "SessionTurnView",
    "SessionView",
    "TicketProviderName",
    "TicketProviderView",
    "TicketRef",
    "TicketSelector",
    "TrackRemoteBranch",
    "UpdateWorkspaceRequest",
    "WorkspaceActivityView",
    "WorkspacePaneView",
    "WorkspacePeekView",
    "WorkspaceStateView",
    "attach_instruction_view",
]
