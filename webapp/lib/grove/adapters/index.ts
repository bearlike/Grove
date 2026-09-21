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
  newestActivityIso,
  peekFromActivity,
  pendingQuestions,
  primarySessionId,
  primarySessionIdOf,
  queueFingerprint,
  streamAction,
  turnsProgressFingerprint,
} from "./activity";
export type { StreamAction } from "./activity";

export {
  ANNOTATION_CODEC,
  annotatedExtension,
  annotatedName,
  annotatedSize,
  ATTACHMENT_ACCEPT,
  attachmentCountError,
  attachmentError,
  attachmentKind,
  dataUrlMimeType,
  fileFromStaged,
  filePartSize,
  MAX_ANNOTATED_EDGE,
  MAX_ATTACHMENT_BYTES,
  MAX_ATTACHMENTS,
  messageAttachments,
  mimeFromName,
  splitAttachments,
} from "./attachments";
export type {
  MessageAttachment,
  StagedAttachment,
  UserMessageBody,
} from "./attachments";

export { baseBranchOf } from "./branch";

export {
  configureMessage,
  DEFAULT_DRAWIO_URL,
  draftFilename,
  drawioEmbedUrl,
  drawioOrigin,
  firstPageId,
  fitMessage,
  flushMessages,
  flushToken,
  loadMessage,
  parseDrawioEvent,
  pngBase64,
  previewMessage,
  previewToken,
  sanitizeDrawioBase,
  savedStatusMessage,
  unsavedStatusMessage,
} from "./drawio";
export type { DrawioEvent } from "./drawio";

export {
  NO_GALLERY_FILTER,
  filterGallery,
  galleryCardCaption,
  galleryCardTitle,
  galleryCountLabel,
  galleryExportFilename,
  galleryExportMessage,
  galleryExportToken,
  galleryOpenTarget,
  galleryPageDocument,
  galleryPages,
  galleryProjects,
  galleryViewerUrl,
} from "./gallery";
export type { GalleryFilter, GallerySort } from "./gallery";

export {
  branchPlanFor,
  buildCreateRequest,
  customModelError,
  deriveTitle,
} from "./launch";

export { mergeTurns, turnCursor } from "./turns";
export type { MergeDirection } from "./turns";
export type { HeldWindow, TurnMerge, TurnWindow } from "./turns";

export { questionGroups, questionPresentation } from "./question";
export type {
  ApprovalCardProps,
  ElicitationFormProps,
  QuestionPresentation,
} from "./question";

export {
  activeIndex,
  inPhaseProgress,
  phaseEnteredAt,
  reportIsStale,
  STALE_REPORT_MS,
  stepIsLive,
  stepState,
} from "./phase-progress";
export type { InPhaseProgress, StepState } from "./phase-progress";

export {
  agentStatusProps,
  connectionStateProps,
  formatElapsed,
} from "./status";
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
  shouldFetchToolBody,
  toolCallFields,
  toolCallStatus,
  toolCallStatusLabel,
} from "./tool-call";
export type { ToolCallField, ToolCallView } from "./tool-call";

export {
  GROVE_DATA_NAME,
  GROVE_DATA_PART,
  messagesFromTurns,
} from "./transcript";
export type {
  CompactionPartData,
  ContinuationPartData,
  FileEditPartData,
  GroveDataName,
  NotePartData,
  NotificationPartData,
  QuestionPartData,
} from "./transcript";

export {
  accountSummaries,
  accountSummary,
  EMPTY_CONTEXT,
  EMPTY_COUNTS,
  fitAccounts,
  fleetCounts,
  hiddenNeedsAttention,
  MAX_INLINE_ACCOUNTS,
  EMPTY_PROGRESS,
  fleetAttention,
  fleetProgress,
  hasGitActivity,
  percentLabel,
  projectContext,
  projectSubpath,
  quotaSummary,
  sessionSummary,
  systemFacts,
  tightestWindow,
  workspaceContext,
} from "./footer";
export type {
  AccountFit,
  AccountSummary,
  FleetCounts,
  FleetProgress,
  FooterContext,
  GitFacts,
  QuotaSummary,
  SessionSummary,
  SystemFacts,
} from "./footer";
