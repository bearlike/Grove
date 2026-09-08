import Link from "next/link";
import { TriangleAlertIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { SectionCard } from "@/components/grove/card";
import { BranchLabel, PROJECT_MIN_WIDTH, ProjectLabel } from "@/components/grove/entity";
import { RelativeTime } from "@/components/grove/relative-time";
import { OverflowText } from "@/components/grove/overflow-text";
import { WorkingMark } from "@/components/grove/working-loader";
import { cn } from "@/lib/utils";
import { Separator } from "@/components/ui/separator";
import { baseBranchOf } from "@/lib/grove/adapters";
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
import { agentStateOf, lastActivityIso, isInactive } from "./filter";
import { agentExited } from "../workspace/selectors";
import { WorkspaceMetrics } from "./workspace-metrics";
import { workspaceStatusText } from "./workspace-status";
import type { WorkspaceActivity } from "./types";

/** Identity → reported status → change ledger → provenance. Each set wraps alone. */
export function WorkspaceCard({
  workspace,
  repoName,
  grouped = false,
}: {
  workspace: WorkspaceActivity;
  repoName: string;
  grouped?: boolean;
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

  return (
    <SectionCard
      data-testid="workspace-card"
      data-workspace-id={state.id}
      data-attention={workspace.needs_attention}
      data-repo-name={repoName}
      className={cn(isInactive(workspace) && "opacity-80")}
      icon={<AgentMark agentName={state.agent_name} />}
      title={
        <Link href={`/w/${state.id}`} className="block min-w-0 text-content-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2">
          <OverflowText>{state.title}</OverflowText>
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
          <StatusBadge status={state.status} />
          {working ? <WorkingMark /> : null}
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
          <RelativeTime iso={lastActivityIso(workspace)} className="max-w-16 shrink-0 truncate tabular-nums" />
          <OwnedSessionBadge native={state.native} />
          <RuntimeBadge runtime={state.runtime} fallbackReason={state.runtime_fallback_reason} />
        </footer>
      </div>
    </SectionCard>
  );
}
