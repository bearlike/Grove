"use client";

import { useState } from "react";
import { BotIcon, CircleCheckIcon, CircleDotIcon, ClockIcon, EyeIcon, TriangleAlertIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { CardDisclosure, CardScroll, CardShell } from "@/components/grove/card";
import { RelativeTime } from "@/components/grove/relative-time";
import { AgentStateBadge } from "@/components/grove/fleet/badges";
import type { AgentState } from "@/components/grove/fleet/types";
import type { SubagentFleetData } from "@/lib/grove/hooks";
import { cn } from "@/lib/utils";

export type FleetSummary = { running: number; attention: number; settled: number };

/** One child, whichever source described it. */
export type FleetRow = {
  id: string;
  label: string;
  state: AgentState;
  detail: string | null;
  lastEventAt: string | null;
  /** Present only for a child with a real transcript — a hook-only row has none yet. */
  sessionId: string | null;
};

/**
 * The union of both sources, never a lookup that can drop one.
 *
 * The engine guarantees `subagents` and `sessions` are disjoint by id — a child
 * that has grown a transcript appears once, in `sessions`, and drops out of the
 * hook roster (`agent_id === session.session_id` for the shared case) — so
 * concatenation is the whole join. A row described only in `sessions` (settled
 * before this card ever read the hook feed) must still render, not be dropped
 * for lacking a hook counterpart.
 */
export function fleetRows(fleet: SubagentFleetData): FleetRow[] {
  const hookRows: FleetRow[] = fleet.subagents.map((member) => ({
    id: member.agent_id,
    label: humanize(member.agent_id),
    state: member.state,
    detail: member.current_tool ?? member.last_message,
    lastEventAt: member.last_event_at,
    sessionId: null,
  }));
  const sessionRows: FleetRow[] = fleet.sessions.map((entry) => ({
    id: entry.session.session_id,
    label: humanize(entry.session.session_id),
    state: entry.activity.state,
    detail: entry.activity.current_task,
    lastEventAt: entry.activity.last_event_at,
    sessionId: entry.session.session_id,
  }));
  return [...hookRows, ...sessionRows];
}

function humanize(id: string): string {
  return id.replaceAll(/[-_]+/g, " ");
}

/** The activity state decides grouping; no client-side progress is inferred. */
export function fleetSummary(rows: readonly Pick<FleetRow, "state">[]): FleetSummary {
  return rows.reduce(
    (summary, row) => {
      if (row.state === "working" || row.state === "starting") summary.running += 1;
      else if (row.state === "waiting" || row.state === "blocked" || row.state === "error") {
        summary.attention += 1;
      } else summary.settled += 1;
      return summary;
    },
    { running: 0, attention: 0, settled: 0 },
  );
}

function isSettled(row: FleetRow): boolean {
  return row.state === "idle" || row.state === "unknown";
}

/**
 * One root session's live child roster. It lives beside Queue rather than inside
 * the transcript so a fleet frame writes only this card's independent cache.
 */
export function SubagentFleetPanel({
  fleet,
  workspaceId,
  defaultOpen = false,
  stale = false,
  onOpenTranscript,
}: {
  fleet: SubagentFleetData;
  workspaceId: string;
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

  const rows = fleetRows(fleet);
  if (rows.length === 0) {
    return <FleetState icon={<BotIcon />} label="No subagents active" testId="subagent-fleet-empty" />;
  }

  const summary = fleetSummary(rows);
  const active = rows.filter((row) => !isSettled(row));
  const settled = rows.filter(isSettled);

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
            <FleetCounts summary={summary} stale={stale} />
          </div>
        }
      >
        <CardScroll className="divide-y divide-border" data-testid="subagent-fleet-list">
          {active.map((row) => (
            <SubagentRow key={row.id} row={row} workspaceId={workspaceId} onOpenTranscript={onOpenTranscript} />
          ))}
          {settled.length > 0 && (
            <SettledGroup rows={settled} workspaceId={workspaceId} onOpenTranscript={onOpenTranscript} />
          )}
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
    summary.attention > 0 ? `${summary.attention} awaiting input` : null,
  ].filter((label): label is string => label !== null);
  return (
    <span className="shrink-0 text-xs text-content-tertiary" data-testid="subagent-fleet-summary">
      {stale ? "Updates disconnected" : labels.join(" · ") || `${summary.settled} settled`}
    </span>
  );
}

function SubagentRow({
  row,
  workspaceId,
  onOpenTranscript,
}: {
  row: FleetRow;
  workspaceId: string;
  onOpenTranscript: (sessionId: string, label: string) => void;
}): React.ReactNode {
  return (
    <div className="flex min-w-0 items-start gap-2 px-3 py-2" data-testid="subagent-fleet-member">
      <CircleDotIcon aria-hidden className="mt-0.5 size-3.5 shrink-0 text-content-tertiary" />
      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <span className="min-w-0 truncate text-sm font-medium">{row.label}</span>
          <AgentStateBadge state={row.state} />
        </div>
        {row.detail && <p className="truncate text-xs text-content-secondary">{row.detail}</p>}
        {row.lastEventAt && (
          <p className="flex items-center gap-1 text-xs text-content-tertiary">
            <ClockIcon aria-hidden className="size-3" />
            <RelativeTime iso={row.lastEventAt} />
          </p>
        )}
      </div>
      {row.sessionId && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="shrink-0"
          onClick={() => onOpenTranscript(row.sessionId!, row.label)}
          data-testid="subagent-open-transcript"
          aria-label={`Open ${row.label} transcript in ${workspaceId}`}
        >
          <EyeIcon aria-hidden />
          <span>Open transcript</span>
        </Button>
      )}
    </div>
  );
}

function SettledGroup({
  rows,
  workspaceId,
  onOpenTranscript,
}: {
  rows: readonly FleetRow[];
  workspaceId: string;
  onOpenTranscript: (sessionId: string, label: string) => void;
}): React.ReactNode {
  const [open, setOpen] = useState(false);
  return (
    <div data-testid="subagent-fleet-settled">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-content-tertiary"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
      >
        <CircleCheckIcon aria-hidden className="size-3.5" />
        Settled {rows.length}
      </button>
      {open && (
        <div className="divide-y divide-border">
          {rows.map((row) => (
            <div
              key={row.id}
              className="flex min-w-0 items-center justify-between gap-2 px-3 py-2 text-xs text-content-tertiary"
            >
              <span className={cn("min-w-0 truncate")}>{row.label}</span>
              {row.sessionId && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="shrink-0"
                  onClick={() => onOpenTranscript(row.sessionId!, row.label)}
                  data-testid="subagent-open-transcript"
                  aria-label={`Open ${row.label} transcript in ${workspaceId}`}
                >
                  <EyeIcon aria-hidden />
                  <span>Open transcript</span>
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
