import Link from "next/link";
import { GitCommitHorizontal, Radio } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { StatusBadge } from "@/components/workspace/status-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { AgentBadge } from "@/components/dashboard/running-indicator";
import { AgentStateBadge } from "@/components/dashboard/agent-state-badge";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import { tierForActivity } from "@/lib/grove/activity-tier";
import { cn } from "@/lib/utils";
import type { WorkspaceActivityView } from "@/lib/grove/types";

/**
 * One Activity-Dashboard tile, built for glanceability: every card carries the
 * SAME four slots top to bottom, so the eye learns one scan path —
 *
 *   (a) identity   — agent brand badge · workspace title (link) · project chip
 *                    · ONE state badge (agent state; workspace StatusBadge only
 *                    when there is no session or the lifecycle itself is broken)
 *   (b) happening  — what the agent is doing right now (`current_task`, falling
 *                    back to the session's durable self-name), plus an
 *                    "· N bg agents" suffix while subagents run
 *   (c) metrics    — muted one-liner: turns · tools · tokens
 *   (d) durable    — last commit subject + relative time
 *
 * The whole card takes the activity-tier treatment (single policy site:
 * `tierForActivity`) — dormant cards dim, attention cards stay full-opacity
 * with a highlight ring, blocked is loudest via the badge.
 *
 * Test seam: `data-testid="session-card"` + `data-agent-state` + `data-tier`,
 * `agent-state-badge`/`agent-state-label`, `project-chip`, `happening-now`,
 * `metrics`, `last-commit`, `live-toggle` (WORKING-gated), and the title link.
 */
export function SessionCard({
  activity,
  projectName,
  liveOpen = false,
  onToggleLive,
}: {
  activity: WorkspaceActivityView;
  /** Project identity chip — the flat wall has no group headers to carry it. */
  projectName?: string;
  liveOpen?: boolean;
  onToggleLive?: (id: string) => void;
}) {
  const s = activity.state;
  const primarySession = activity.sessions[0] ?? null;
  const primary = primarySession?.activity ?? null;
  // Shared read-model: "happening now" precedence, metrics, and the subagent
  // fallback live in AgentLiveStatus so this card and the detail context bar
  // can never disagree on what the agent is doing.
  const live = AgentLiveStatus.of(primary);
  const agentState = live.state;
  const { tier, opacityClass, accentVar, treatment } = tierForActivity(
    primary?.state ?? null,
    s.status,
  );

  // (b) live signal first, durable session self-name as the quiet fallback.
  const happening = live.taskLine;
  const subagents = live.subagents;

  const lastCommit = activity.recent_commits[0] ?? null;
  const canGoLive = agentState === "working" && onToggleLive != null;
  // Lifecycle badge only where it is itself the signal: no agent session to
  // badge, or a broken lifecycle state worth surfacing over the agent axis.
  const showStatus = primary == null || s.status === "orphaned" || s.status === "error";

  return (
    <Card
      data-testid="session-card"
      data-agent-state={agentState}
      data-tier={tier}
      className={cn(
        "flex h-full flex-col gap-2 p-3 transition-[box-shadow,border-color,opacity] duration-200 hover:shadow-md",
        opacityClass,
        // Attention (waiting/blocked/error) is highlighted with a ring, never dimmed.
        treatment === "ring" && "ring-2 ring-offset-0",
        liveOpen && "ring-2 ring-ring",
      )}
      style={
        treatment === "ring" && !liveOpen
          ? { borderColor: accentVar, ["--tw-ring-color" as string]: accentVar }
          : undefined
      }
    >
      {/* (a) identity: who · which workspace · which project · what state */}
      <div className="flex items-center gap-2">
        <AgentBadge
          agentName={s.agent_name}
          adapterKind={primarySession?.session.adapter_kind}
          primaryState={primary?.state ?? null}
          workspaceStatus={s.status}
          className="shrink-0"
        />
        <Link
          href={`/w/${encodeURIComponent(s.id)}`}
          title={`${s.title} — ${s.branch}`}
          className="min-w-0 flex-1 truncate text-sm font-semibold leading-tight hover:underline focus-visible:underline focus-visible:outline-none"
        >
          {s.title}
        </Link>
        {projectName && (
          <Badge
            variant="outline"
            data-testid="project-chip"
            className="shrink-0 rounded-full bg-muted/40 px-2 py-0 font-mono text-[10px] font-normal text-muted-foreground"
          >
            {projectName}
          </Badge>
        )}
        {showStatus ? <StatusBadge status={s.status} size="sm" /> : <AgentStateBadge state={agentState} />}
      </div>

      {/* (b) happening now — the glance target. Error detail takes the slot
          over a stale current_task: WHY it stopped beats what it last did. */}
      {live.errorDetail ? (
        <p
          data-testid="happening-now"
          className="line-clamp-2 font-mono text-xs"
          style={{ color: "var(--agent-error)" }}
          title={live.errorDetail}
        >
          {live.errorDetail}
        </p>
      ) : (
        <p
          data-testid="happening-now"
          className={cn(
            "line-clamp-2 text-xs",
            happening ? "text-foreground/90" : "italic text-muted-foreground/70",
          )}
          title={happening ?? undefined}
        >
          {happening ?? (primary ? "no activity yet" : "no agent session")}
          {subagents > 0 && (
            <span className="text-muted-foreground">
              {" · "}
              {subagents} bg agent{subagents > 1 ? "s" : ""}
            </span>
          )}
        </p>
      )}

      {/* (c) compact metrics, muted — reference data, never the headline */}
      <div className="flex items-center justify-between gap-2">
        <span
          data-testid="metrics"
          className="truncate font-mono text-[11px] tabular-nums text-muted-foreground"
        >
          {live.metricsLine ?? "—"}
        </span>
        {canGoLive && (
          <button
            type="button"
            data-testid="live-toggle"
            aria-pressed={liveOpen}
            onClick={() => onToggleLive?.(s.id)}
            className={cn(
              "inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              liveOpen
                ? "bg-[var(--agent-working)]/20 text-[var(--agent-working)]"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Radio className="size-3" aria-hidden />
            Live
          </button>
        )}
      </div>

      {/* (d) durable: the last commit — what was actually done, and when */}
      <div
        data-testid="last-commit"
        className="mt-auto flex min-w-0 items-baseline gap-1.5 border-t border-border pt-2 text-xs text-muted-foreground"
      >
        <GitCommitHorizontal aria-hidden className="size-3 shrink-0 self-center" />
        {lastCommit ? (
          <>
            <span className="min-w-0 truncate font-mono" title={lastCommit.subject}>
              {lastCommit.subject}
            </span>
            <span className="ml-auto shrink-0 whitespace-nowrap text-[11px]">
              <RelativeTime iso={lastCommit.committed_at} />
            </span>
          </>
        ) : (
          <span className="italic text-muted-foreground/70">no commits yet</span>
        )}
      </div>
    </Card>
  );
}
