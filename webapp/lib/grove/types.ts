import type { components } from "./types.gen";

export type WorkspaceStateView = components["schemas"]["WorkspaceStateView"];
export type WorkspacePeekView = components["schemas"]["WorkspacePeekView"];
export type CommitSummaryView = components["schemas"]["CommitSummaryView"];
export type AttachInstructionView = components["schemas"]["AttachInstructionView"];
export type WorkspaceStatus = components["schemas"]["WorkspaceStatus"];
export type BranchProvenance = components["schemas"]["BranchProvenance"];
export type Placement = components["schemas"]["Placement"];
export type InitStatus = components["schemas"]["InitStatus"];
export type HealthView = components["schemas"]["HealthView"];
export type WhoamiView = components["schemas"]["WhoamiView"];

// Activity Dashboard wire shapes (epic #11). Generated from the daemon's
// /activity + /events schemas; consumed by the dashboard page + SSE hook.
export type DashboardSnapshotView = components["schemas"]["DashboardSnapshotView"];
export type DashboardEvent = components["schemas"]["DashboardEvent"];
export type ProjectGroupView = components["schemas"]["ProjectGroupView"];
export type WorkspaceActivityView = components["schemas"]["WorkspaceActivityView"];
export type SessionActivityView = components["schemas"]["SessionActivityView"];
export type AgentActivityView = components["schemas"]["AgentActivityView"];
export type AgentSessionView = components["schemas"]["AgentSessionView"];
export type AgentActivityState = components["schemas"]["AgentActivityState"];
export type WorkspacePaneView = components["schemas"]["WorkspacePaneView"];

// Session drill-down wire shapes (workspace detail's Sessions panel).
export type SessionSummaryView = components["schemas"]["SessionSummaryView"];
export type SessionDetailView = components["schemas"]["SessionDetailView"];
export type SessionTurnView = components["schemas"]["SessionTurnView"];
