/**
 * Every server-state subscription in the app.
 *
 * Components consume hooks; components never fetch. Import from here, not from
 * the modules underneath — the split between queries, mutations, stream and
 * usage is internal and free to move.
 */

export { groveClient, POLL_MS } from "./client";
export { useSubagentFleet, useSubagentFleetStream } from "./subagent-fleet";
export type { SubagentFleetData } from "./subagent-fleet";
export { usePublicDiff, usePublicTurns, usePublicWorkspace } from "./public";
export { groveKeys } from "./keys";
export { ToolBodyProvider, useToolBody, useToolBodySource } from "./tool-body";
export type { ToolBodySource } from "./tool-body";
export { useArtifactContent, useAttachArtifact, useDetachArtifact } from "./artifacts";
export { useGallery, useGalleryDocument, useGalleryPreview, useSaveGalleryPreview } from "./gallery";
export { useAddProject, useProjectCandidates } from "./projects";
export type { AddProjectRequest } from "./projects";

export {
  useAgents,
  useBranches,
  useCatalogTurns,
  useChildTurns,
  useDiagramWriter,
  useWorkspaceDiagram,
  useHealth,
  useMailboxContacts,
  useModels,
  useProvisionProgress,
  useSessionCatalog,
  useSessionControls,
  useSharePolicy,
  useSessionTurns,
  useTicketProviders,
  useTickets,
  useMe,
  useWhoami,
  useWorkspace,
  useWorkspaceCommits,
  useWorkspaceDiff,
  useWorkspacePanels,
  useWorkspaceActivity,
  useWorkspacePeek,
  useWorkspaceQueue,
  useWorkspaceSessionCandidates,
  useWorkspaceSessions,
  useWorkspaceHistory,
  useWorkspaceTodo,
  useWorkspaceWatches,
  ticketKey,
} from "./queries";
export type { SessionTurnsQuery, TicketResolutions } from "./queries";

export {
  useAssignedTickets,
  useSaveDefaults,
  useWorkspaceDefaults,
} from "./launch";
export type {
  DefaultsScope,
  SaveDefaultsInput,
  TicketRef,
  WorkspaceDefaultsSaveView,
  WorkspaceDefaultsView,
} from "./launch";

export {
  useAnswerQuestion,
  useCreateWorkspace,
  useInterrupt,
  useInvokeControl,
  useRemapSession,
  useSendMessage,
  useSaveSharePolicy,
  useSendKey,
  useSwitchEffort,
  useSwitchModel,
  useBulkWorkspaceActions,
  useUpdateWorkspace,
  useWorkspaceActions,
  withOptimisticSend,
} from "./mutations";
export type { AnswerQuestionInput, BulkOutcome, BulkVerb, SendMessageInput, WorkspaceActions } from "./mutations";

export {
  backstopInterval,
  GroveStreamProvider,
  useActivityStream,
  useTranscriptInvalidation,
  useWorkspacePane,
} from "./stream";
export type { ActivityStream } from "./stream";

export {
  useRefreshUsage,
  useUsageActivity,
  useUsageBashCommands,
  useUsageBreakdown,
  useUsageFindings,
  useUsageQuotas,
  useUsageSeries,
  useUsageSessions,
  useUsageSummary,
} from "./usage";
export type { UsageFilters } from "./usage";
