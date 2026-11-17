import { memo } from "react";
import Link from "next/link";
import { TriangleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { SectionCard } from "@/components/grove/card";
import { BranchLabel, PROJECT_MIN_WIDTH, ProjectLabel } from "@/components/grove/entity";
import { LoopingText, OverflowText, ScannedTextScope } from "@/components/grove/overflow-text";
import { WorkingMark } from "@/components/grove/working-loader";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { baseBranchOf, lastTranscriptIso, replyCount } from "@/lib/grove/adapters";
import {
  AgentStateBadge,
  ExitedSessionBadge,
  OwnedSessionBadge,
  PhaseBadge,
  RuntimeBadge,
  StatusBadge,
  TicketChip,
  TodoBadge,
} from "./badges";
import { agentStateOf, isInactive } from "./filter";
import { agentExited } from "../workspace/selectors";
import { SessionTimes, UnreadDot, WorkspaceMetrics } from "./workspace-metrics";
import { useHasUnread } from "./unread";
import { workspaceStatusText } from "./workspace-status";
import type { WorkspaceActivity } from "./types";

/** Identity → reported status → change ledger → provenance. Each set wraps alone. */
export const WorkspaceCard = memo(function WorkspaceCard({
  workspace,
  repoName,
  grouped = false,
  selected,
  onSelectedChange,
}: {
  workspace: WorkspaceActivity;
  repoName: string;
  grouped?: boolean;
  /**
   * Bulk selection, offered only when the surface has bulk verbs. Both or
   * neither: a checkbox with no handler is a control that does nothing.
   */
  selected?: boolean;
  onSelectedChange?: (id: string, selected: boolean) => void;
}): React.ReactNode {
  const { state } = workspace;
  const base = baseBranchOf(state);
  const status = workspaceStatusText(workspace);
  const agentState = agentStateOf(workspace);
  const exitReason = agentExited(workspace);
  // The mark rides AFTER the status badge, for the reason the rail puts it last
  // in its title band: the badge is a standing fact about the workspace, the
  // mark is what is happening right now. One derivation feeds both it and the
  // AgentStateBadge below — never a second read of the activity state.
  const working = agentState === "working";
  const unread = useHasUnread(state.id, replyCount(workspace));

  return (
    <SectionCard
      data-testid="workspace-card"
      data-workspace-id={state.id}
      data-attention={workspace.needs_attention}
      data-repo-name={repoName}
      className={cn(isInactive(workspace) && "opacity-80")}
      icon={<AgentMark agentName={state.agent_name} />}
      title={
        // The workspace's name loops like the rail's rather than resting on an
        // ellipsis; the status line below keeps the bounded pass, because it
        // is a sentence read once rather than a name (`overflow-text.tsx`).
        <Link href={`/w/${state.id}`} className="block min-w-0 text-content-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2">
          <ScannedTextScope>
            <LoopingText>{state.title}</LoopingText>
          </ScannedTextScope>
        </Link>
      }
      description={
        <span className="flex min-w-0 items-center gap-2">
          {!grouped ? <ProjectLabel name={repoName} className={PROJECT_MIN_WIDTH} /> : null}
          <BranchLabel name={state.branch} />
          {base ? (
            <>
              <span className="shrink-0 text-content-tertiary">←</span>
              <BranchLabel name={base} />
            </>
          ) : null}
        </span>
      }
      action={
        <span className="flex min-w-0 items-center gap-1.5">
          {unread ? <UnreadDot /> : null}
          <StatusBadge status={state.status} />
          {working ? <WorkingMark /> : null}
          {/* The native checkbox: `components/ui/` ships no checkbox primitive
              (see `sessions/filter-menu.tsx`), and the kill dialog already
              draws this one. Its floor is a pointer bound, not type. */}
          {onSelectedChange ? (
            <input
              type="checkbox"
              checked={selected ?? false}
              onChange={(event) => onSelectedChange(state.id, event.target.checked)}
              onClick={(event) => event.stopPropagation()}
              aria-label={`Select ${state.title}`}
              className="ml-1 size-4 min-h-[16px] min-w-[16px] shrink-0 cursor-pointer"
              data-testid="workspace-card-select"
            />
          ) : null}
        </span>
      }
      flush
    >
      <div className="flex min-w-0 flex-col gap-1.5 px-3 py-2" data-testid="card-status">
        <div className="flex flex-wrap items-center gap-1" data-testid="card-marks">
          <AgentStateBadge state={agentState} />
          <ExitedSessionBadge reason={exitReason} />
          <PhaseBadge phase={workspace.phase} />
          {workspace.todo ? <span className="ml-auto" data-testid="card-checklist"><TodoBadge todo={workspace.todo} /></span> : null}
        </div>
        <p className="min-w-0 text-sm text-content-secondary" data-testid="card-status-text" aria-label="Grove status">
          <OverflowText>{status ?? "No Grove status reported yet."}</OverflowText>
        </p>
        {state.error_detail ? (
          <p className="flex items-start gap-2 text-sm text-content-secondary" role="alert" data-testid="card-error">
            <TriangleAlertIcon aria-hidden className="mt-0.5 size-4 shrink-0 text-destructive" />
            <span className="line-clamp-2 break-words" title={state.error_detail}>{state.error_detail}</span>
          </p>
        ) : null}
      </div>

      <div className="mt-auto flex flex-col" data-testid="card-ledger">
        <Separator />
        <div className="flex flex-col gap-2 px-3 py-2">
          <WorkspaceMetrics workspace={workspace} />
          {state.ticket_refs.length > 0 ? (
            <div className="flex flex-wrap items-center gap-1.5" data-testid="card-tickets">
              {state.ticket_refs.map((ticket) => (
                <TicketChip key={`${ticket.provider}#${ticket.id}`} ticket={ticket} />
              ))}
            </div>
          ) : null}
        </div>
        <Separator />
        <footer className="flex min-w-0 items-center justify-between gap-2 px-3 py-1.5 text-xs text-content-tertiary" data-testid="card-footer">
          <span className="min-w-0 flex-1 truncate" title={state.agent_name}>{state.agent_name}</span>
          <SessionTimes
            createdIso={state.created_at}
            updatedIso={lastTranscriptIso(workspace)}
          />
          <OwnedSessionBadge native={state.native} />
          <RuntimeBadge runtime={state.runtime} fallbackReason={state.runtime_fallback_reason} />
        </footer>
      </div>
    </SectionCard>
  );
});
