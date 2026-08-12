/**
 * Every server-state subscription in the app.
 *
 * Components consume hooks; components never fetch. Import from here, not from
 * the modules underneath — the split between queries, mutations, stream and
 * usage is internal and free to move.
 */

export { groveClient, POLL_MS } from "./client";
export { groveKeys } from "./keys";

export {
  useAgents,
  useBranches,
  useCatalogTurns,
  useHealth,
  useProvisionProgress,
  useSessionCatalog,
  useSessionControls,
  useSessionTurns,
  useTicketProviders,
  useTickets,
  useWhoami,
  useWorkspace,
  useWorkspaceCommits,
  useWorkspaceDiff,
  useWorkspacePeek,
  useWorkspaceQueue,
  useWorkspaceSessionCandidates,
  useWorkspaceSessions,
  useWorkspaceTodo,
  ticketKey,
} from "./queries";
export type { SessionTurnsQuery, TicketResolutions } from "./queries";

export {
  useAnswerQuestion,
  useCreateWorkspace,
  useInterrupt,
  useInvokeControl,
  useRemapSession,
  useSendMessage,
  useSwitchModel,
  useWorkspaceActions,
  withOptimisticSend,
} from "./mutations";
export type { AnswerQuestionInput, WorkspaceActions } from "./mutations";

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
