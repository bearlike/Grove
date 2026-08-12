/**
 * Pure Grove-wire → assistant-ui adapters.
 *
 * Every export here is a plain function of its arguments: no React, no fetching,
 * no clock. That is the whole point — the mapping from Grove's daemon shapes to
 * the vendored components' props is exercisable with zero daemon, and the hooks
 * in `../hooks` stay thin enough to have no logic worth testing.
 */

export {
  applyDashboardEvent,
  applyWorkspaceState,
  allWorkspaces,
  commitsFingerprint,
  dropWorkspace,
  findWorkspaceActivity,
  lastActivityAt,
  lastActivityIso,
  peekFromActivity,
  pendingQuestions,
  primarySessionId,
  queueFingerprint,
  streamAction,
  turnsProgressFingerprint,
} from "./activity";
export type { StreamAction } from "./activity";

export { baseBranchOf } from "./branch";

export { mergeTurns, turnCursor } from "./turns";
export type { HeldWindow, TurnMerge, TurnWindow } from "./turns";

export { questionGroups, questionPresentation } from "./question";
export type {
  ApprovalCardProps,
  ElicitationFormProps,
  QuestionPresentation,
} from "./question";

export { agentStatusProps, connectionStateProps, formatElapsed } from "./status";
export type {
  AgentActivityState,
  AgentStatusProps,
  ConnectionStateProps,
  TaskPhase,
} from "./status";

export {
  DEFAULT_GROUPING,
  MAX_SERIES,
  MIN_FORECAST_DAYS,
  SERIES_GROUPINGS,
  ceilingLabelPositions,
  groupingFor,
  tickLabel,
  trendLine,
  usageSeriesChart,
} from "./usage-series";
export type {
  CeilingLabelPosition,
  SeriesChartRow,
  SeriesGrouping,
  SeriesSlot,
  UsageSeriesChart,
} from "./usage-series";

export { agentPlanProps, todoListProps } from "./todo";
export type { AgentPlanProps, TodoListProps } from "./todo";

export {
  asToolCall,
  formatToolDuration,
  toolCallFields,
  toolCallStatus,
  toolCallStatusLabel,
} from "./tool-call";
export type { ToolCallField, ToolCallView } from "./tool-call";

export { GROVE_DATA_NAME, GROVE_DATA_PART, messagesFromTurns } from "./transcript";
export type {
  CompactionPartData,
  ContinuationPartData,
  FileEditPartData,
  GroveDataName,
  NotePartData,
  NotificationPartData,
  QuestionPartData,
} from "./transcript";
