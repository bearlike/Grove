"use client";

import { useState } from "react";
import { BotIcon, ClockIcon, EyeIcon, TimerIcon, TriangleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { CardDisclosure, CardScroll, CardShell } from "@/components/grove/card";
import { RelativeTime } from "@/components/grove/relative-time";
import { SubagentRunBadge } from "@/components/grove/fleet/badges";
import type { SubagentRun } from "@/components/grove/fleet/tokens";
import type { AgentState } from "@/components/grove/fleet/types";
import type { SubagentFleetData } from "@/lib/grove/hooks";

export type FleetSummary = { running: number; finished: number; stopped: number };

/** One child, whichever source described it. */
export type FleetRow = {
  id: string;
  /** What the child was asked to do — the row's title when the provider said. */
  description: string | null;
  /** Grove's own reading of the child's run, never the agent axis's attention. */
  run: SubagentRun;
  startedAt: string | null;
  lastEventAt: string | null;
  /** Present only for a child with a real transcript — a hook-only row has none yet. */
  sessionId: string | null;
};

/**
 * A subagent's run, from the only two facts that can decide it.
 *
 * `working`/`starting` is running. Every other state is the child's turn having
 * closed, which means it returned its result — `finished` — with two exceptions
 * that mean it did not: an `error`, and a child the parent session has stopped
 * waiting on. The second is the case the per-child state cannot see on its own:
 * a child killed mid-tool leaves its transcript reading `working` forever, so
 * while the ROOT has closed its turn (`parentWorking` false) a child still
 * claiming to work was stopped, not running. `parentWorking` is `null` when the
 * root's state is unknown, and then the child's own claim stands.
 *
 * There is no "waiting for you" here on purpose: a subagent answers its parent,
 * not a person, so a closed turn is completion rather than a question.
 */
export function subagentRun(state: AgentState, parentWorking: boolean | null): SubagentRun {
  if (state === "working" || state === "starting") {
    return parentWorking === false ? "stopped" : "running";
  }
  return state === "error" ? "stopped" : "finished";
}

/**
 * The union of both sources, never a lookup that can drop one — ordered so the
 * runs still in flight lead and everything else reads newest first.
 *
 * The engine guarantees `subagents` and `sessions` are disjoint by id — a child
 * that has grown a transcript appears once, in `sessions`, and drops out of the
 * hook roster (`agent_id === session.session_id` for the shared case) — so
 * concatenation is the whole join. A row described only in `sessions` (settled
 * before this card ever read the hook feed) must still render, not be dropped
 * for lacking a hook counterpart.
 */
export function fleetRows(fleet: SubagentFleetData, parentWorking: boolean | null = null): FleetRow[] {
  const hookRows: FleetRow[] = fleet.subagents.map((member) => ({
    id: member.agent_id,
    description: member.agent_type,
    run: subagentRun(member.state, parentWorking),
    startedAt: member.started_at,
    lastEventAt: member.last_event_at,
    sessionId: null,
  }));
  const sessionRows: FleetRow[] = fleet.sessions.map((entry) => ({
    id: entry.session.session_id,
    // `current_task` is the child's own description (its sidecar, else its
    // first prompt); `title` is the teammate's chosen name or agent type.
    description: entry.activity.current_task ?? entry.activity.title,
    run: subagentRun(entry.activity.state, parentWorking),
    startedAt: entry.activity.started_at ?? null,
    lastEventAt: entry.activity.last_event_at,
    sessionId: entry.session.session_id,
  }));
  return [...hookRows, ...sessionRows].sort(byRunThenRecency);
}

/** Running first; then the most recently active; a row with no clock sinks. */
function byRunThenRecency(a: FleetRow, b: FleetRow): number {
  const running = Number(b.run === "running") - Number(a.run === "running");
  if (running !== 0) return running;
  return instant(b.lastEventAt) - instant(a.lastEventAt);
}

function instant(iso: string | null): number {
  const ms = iso ? Date.parse(iso) : Number.NaN;
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms;
}

export function fleetSummary(rows: readonly Pick<FleetRow, "run">[]): FleetSummary {
  const summary: FleetSummary = { running: 0, finished: 0, stopped: 0 };
  for (const row of rows) summary[row.run] += 1;
  return summary;
}

/**
 * One root session's child roster. It lives beside Queue rather than inside
 * the transcript so a fleet frame writes only this card's independent cache.
 */
export function SubagentFleetPanel({
  fleet,
  workspaceId,
  parentWorking = null,
  defaultOpen = false,
  stale = false,
  onOpenTranscript,
}: {
  fleet: SubagentFleetData;
  workspaceId: string;
  /** Whether the ROOT session is working; `null` when it is not known. */
  parentWorking?: boolean | null;
  defaultOpen?: boolean;
  stale?: boolean;
  onOpenTranscript: (sessionId: string, label: string) => void;
}): React.ReactNode {
  const [open, setOpen] = useState(defaultOpen);

  if (fleet.error) {
    return <FleetState icon={<TriangleAlertIcon />} label={fleet.error} testId="subagent-fleet-error" />;
  }
  if (!fleet.supported) {
    return <FleetState icon={<BotIcon />} label="Subagents unavailable" testId="subagent-fleet-unsupported" />;
  }

  const rows = fleetRows(fleet, parentWorking);
  if (rows.length === 0) {
    return <FleetState icon={<BotIcon />} label="No subagents yet" testId="subagent-fleet-empty" />;
  }

  return (
    <CardShell data-testid="subagent-fleet-card">
      <CardDisclosure
        open={open}
        onOpenChange={setOpen}
        header
        summary={
          <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-2 text-sm font-medium">
              <BotIcon aria-hidden className="size-4 shrink-0 text-content-tertiary" />
              <span>Subagents</span>
            </span>
            <FleetCounts summary={fleetSummary(rows)} stale={stale} />
          </div>
        }
      >
        <CardScroll className="divide-y divide-border" data-testid="subagent-fleet-list">
          {rows.map((row) => (
            <SubagentRow key={row.id} row={row} workspaceId={workspaceId} onOpenTranscript={onOpenTranscript} />
          ))}
        </CardScroll>
      </CardDisclosure>
    </CardShell>
  );
}

function FleetState({ icon, label, testId }: { icon: React.ReactNode; label: string; testId: string }): React.ReactNode {
  return (
    <p className="flex items-center gap-2 text-xs text-content-tertiary" data-testid={testId}>
      <span className="[&_svg]:size-4">{icon}</span>
      {label}
    </p>
  );
}

function FleetCounts({ summary, stale }: { summary: FleetSummary; stale: boolean }): React.ReactNode {
  const labels = [
    summary.running > 0 ? `${summary.running} running` : null,
    summary.finished > 0 ? `${summary.finished} finished` : null,
    summary.stopped > 0 ? `${summary.stopped} stopped` : null,
  ].filter((label): label is string => label !== null);
  return (
    <span className="shrink-0 text-xs text-content-tertiary" data-testid="subagent-fleet-summary">
      {stale ? "Updates disconnected" : labels.join(" · ")}
    </span>
  );
}

/**
 * Description first, identity last: the description says what the child was
 * asked to do, which is what a reader scans for; the id is only an address.
 * A row whose provider named no description is titled by its id instead.
 */
function SubagentRow({
  row,
  workspaceId,
  onOpenTranscript,
}: {
  row: FleetRow;
  workspaceId: string;
  onOpenTranscript: (sessionId: string, label: string) => void;
}): React.ReactNode {
  const title = row.description ?? row.id;
  return (
    <div className="flex min-w-0 items-start gap-2 px-3 py-2" data-testid="subagent-fleet-member" data-run={row.run}>
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 truncate text-sm font-medium" title={title} data-testid="subagent-title">
            {title}
          </span>
          <SubagentRunBadge run={row.run} />
        </div>
        <p className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-content-tertiary">
          {row.startedAt && (
            <span className="flex items-center gap-1" data-testid="subagent-started">
              <TimerIcon aria-hidden className="size-3" />
              Started <RelativeTime iso={row.startedAt} />
            </span>
          )}
          {row.lastEventAt && (
            <span className="flex items-center gap-1" data-testid="subagent-last-active">
              <ClockIcon aria-hidden className="size-3" />
              {row.run === "running" ? "Active" : "Last active"} <RelativeTime iso={row.lastEventAt} />
            </span>
          )}
        </p>
        {row.description && (
          <p className="truncate font-mono text-xs text-content-tertiary" data-testid="subagent-id">
            {row.id}
          </p>
        )}
      </div>
      {row.sessionId && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => onOpenTranscript(row.sessionId!, title)}
          data-testid="subagent-open-transcript"
          aria-label={`Open ${title} transcript in ${workspaceId}`}
        >
          <EyeIcon aria-hidden />
          <span>Open transcript</span>
        </Button>
      )}
    </div>
  );
}
