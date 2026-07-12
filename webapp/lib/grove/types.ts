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

// Mutation wire shapes (workspace lifecycle parity, #56). The create request +
// its branch-plan discriminated union, the branch/agent listings the create
// form reads to populate its pickers.
export type CreateWorkspaceRequest = components["schemas"]["CreateWorkspaceRequest"];
export type BranchPlan = components["schemas"]["BranchPlan"];
export type BranchInfo = components["schemas"]["BranchInfo"];
export type AgentSummaryView = components["schemas"]["AgentSummaryView"];

// Session drill-down wire shapes (workspace detail's Sessions panel).
export type SessionSummaryView = components["schemas"]["SessionSummaryView"];
export type SessionDetailView = components["schemas"]["SessionDetailView"];
export type SessionTurnView = components["schemas"]["SessionTurnView"];
export type DigestEntryView = components["schemas"]["DigestEntryView"];

// Session control surface (#178) — the enumerated slash commands / skills /
// MCP servers / model catalog behind the work panel's Controls tab, fetched
// on demand from `GET /workspaces/{id}/controls`.
export type SessionControlsView = components["schemas"]["SessionControlsView"];
export type SessionControlView = components["schemas"]["SessionControlView"];

// Session picker + remap/resume (#121). `RemapSessionRequest` is the body for
// `POST /workspaces/{id}/session` (pin an existing session as the tracked
// primary); resume-into-workspace rides the existing `CreateWorkspaceRequest`
// (see `resume_session_id` there) so it needs no separate wire type.
export type RemapSessionRequest = components["schemas"]["RemapSessionRequest"];

// Structured agent question carried on a `DigestEntryView` of role "question"
// (epic #74) — rendered read-only as a choice card; options are a static list,
// not interactive controls (answer-back is future).
export type AgentQuestionView = components["schemas"]["AgentQuestionView"];
export type AgentQuestionOptionView = components["schemas"]["AgentQuestionOptionView"];

// Structured agent todo/plan list carried on a `DigestEntryView` of role "todo"
// (#184) — Claude `TodoWrite` / Codex `update_plan` — rendered as a checklist
// card pinned above the composer (the latest one in the loaded turns).
export type TodoListView = components["schemas"]["TodoListView"];
export type TodoItemView = components["schemas"]["TodoItemView"];
