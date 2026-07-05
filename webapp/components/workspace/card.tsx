"use client";
import Link from "next/link";
import { GitBranch, Radio } from "lucide-react";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "./status-badge";
import { PlacementBadge } from "./placement-badge";
import { StatTrio } from "./stat-trio";
import { RelativeTime } from "@/components/shared/relative-time";
import { MetaRow } from "@/components/shared/meta";
import { AgentStateMark } from "@/components/shared/state-mark";
import { AgentStateBadge } from "@/components/dashboard/agent-state-badge";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import { tierForActivity } from "@/lib/grove/activity-tier";
import { parseCommitSubject } from "@/lib/grove/commit-format";
import { cn } from "@/lib/utils";
import type { WorkspaceActivityView } from "@/lib/grove/types";

/**
 * The ONE workspace card (issue #89, redesigned #96, restructured #98-followup) —
 * THREE explicit regions reading straight off `WorkspaceActivityView`, so a glance
 * resolves identity → activity → durable facts top-to-bottom:
 *
 *   HEADER  (p-4) — `AgentStateMark` glyph · title link · ONE right-aligned status
 *     pill, then a quiet branch sub-row (mono teal `--ref-branch`). The state pill
 *     top-aligns so a wrapped two-line title never shoves it off-row.
 *   BODY    (px-4) — "happening now" (`AgentLiveStatus.taskLine`), `line-clamp-2`
 *     so a long task line stays readable instead of single-truncating to nothing
 *     (error detail wins the slot while erroring).
 *   FOOTER  (muted well, `bg-muted/40 border-t`) — metrics one-liner + the WORKING-
 *     gated Live toggle on row one; a `MetaRow` of diff `StatTrio` · last commit ·
 *     placement on row two. The well lifts the durable facts onto their own tier.
 *
 * Color discipline (deliverable D/E): the card border stays neutral; NO full-card
 * status ring/glow/fill, NO terracotta. Attention (waiting/blocked/error) is a
 * single thin LEFT accent bar in the tier accent var; the footer is the only
 * tinted surface, and only a neutral well. Live focus = a quiet "Live" toggle.
 *
 * Test seams kept stable: `data-testid="workspace-card"` + `data-status` +
 * `data-agent-state` + `data-tier`; `happening-now`, `metrics`, `last-commit`,
 * `live-toggle`, the title `<Link>`, and the status/state/stat-trio atoms.
 */
export function WorkspaceCard({
  activity,
  liveOpen = false,
  onToggleLive,
}: {
  activity: WorkspaceActivityView;
  liveOpen?: boolean;
  onToggleLive?: (id: string) => void;
}) {
  const s = activity.state;
  const primarySession = activity.sessions[0] ?? null;
  const primary = primarySession?.activity ?? null;
  // One read-model: "happening now" precedence, the metrics line, the subagent
  // fallback — shared with the detail context bar so they can never disagree.
  const live = AgentLiveStatus.of(primary);
  const agentState = live.state;
  const { tier, accentVar, treatment } = tierForActivity(primary?.state ?? null, s.status);

  const happening = live.taskLine;
  const subagents = live.subagents;
  const lastCommit = activity.recent_commits[0] ?? null;
  const canGoLive = agentState === "working" && onToggleLive != null;
  // Lifecycle badge only where it is itself the signal: no agent session to
  // badge, or a broken lifecycle state worth surfacing over the agent axis.
  const showStatus = primary == null || s.status === "orphaned" || s.status === "error";
  // Attention (waiting/blocked/error) earns a single thin left accent bar — the
  // only on-card hue, never a full ring/fill (deliverable D).
  const attention = treatment === "ring";

  return (
    <Card
      data-testid="workspace-card"
      data-status={s.status}
      data-agent-state={agentState}
      data-tier={tier}
      className={cn(
        "group flex h-full flex-col overflow-hidden border-border p-0 transition-[transform,border-color] duration-200",
        "hover:-translate-y-px hover:border-border/80",
        // Attention (waiting/blocked/error) = a single thin LEFT accent bar in
        // the tier accent var — the only on-card hue, never a full ring/fill.
        attention && "border-l-2",
        liveOpen && "ring-2 ring-ring",
      )}
      style={attention ? { borderLeftColor: accentVar } : undefined}
    >
      {/* ── HEADER: identity ───────────────────────────────────────────────── */}
      <div className="flex flex-col gap-1.5 p-4 pb-3">
        <div className="flex items-start gap-2">
          <AgentStateMark state={agentState} className="mt-[3px] shrink-0" />
          <Link
            href={`/w/${encodeURIComponent(s.id)}`}
            title={`${s.title} — ${s.branch}`}
            className="line-clamp-2 min-w-0 flex-1 text-sm font-semibold leading-snug text-foreground hover:underline focus-visible:underline focus-visible:outline-none"
          >
            {s.title}
          </Link>
          {showStatus ? (
            <StatusBadge status={s.status} size="sm" />
          ) : (
            <AgentStateBadge state={agentState} />
          )}
        </div>

        {/* Branch sub-row — durable identity given its own quiet line. */}
        <div className="flex min-w-0 items-center gap-1.5 pl-[1.375rem] text-xs">
          <GitBranch aria-hidden className="size-3 shrink-0 text-[var(--ref-branch)]" />
          <span title={s.branch} className="truncate font-mono text-[var(--ref-branch)]">
            {s.branch}
          </span>
        </div>
      </div>

      {/* ── BODY: what the agent is doing now (up to two lines) ─────────────── */}
      <div className="px-4 pb-3">
        {live.errorDetail ? (
          <p
            data-testid="happening-now"
            className="line-clamp-2 text-[13px] leading-snug"
            style={{ color: "var(--agent-error)" }}
            title={live.errorDetail}
          >
            {live.errorDetail}
          </p>
        ) : (
          <p
            data-testid="happening-now"
            className={cn(
              "line-clamp-2 text-[13px] leading-snug",
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
      </div>

      {/* ── FOOTER: durable facts on their own muted well ──────────────────── */}
      <div className="mt-auto flex flex-col gap-1.5 border-t border-border bg-muted/40 px-4 py-2.5">
        <div className="flex items-center justify-between gap-2">
          <span
            data-testid="metrics"
            className="truncate font-mono text-xs tabular-nums text-muted-foreground"
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
                  ? "text-[var(--agent-working)]"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Radio className="size-3" aria-hidden />
              Live
            </button>
          )}
        </div>

        <MetaRow>
          <StatTrio
            ahead={activity.base_ahead}
            behind={activity.base_behind}
            dirty={activity.dirty_files}
          />
          <LastCommit lastCommit={lastCommit} />
          <PlacementBadge placement={s.placement} size="sm" />
        </MetaRow>
      </div>
    </Card>
  );
}

/** The footer's last-commit slot — parsed tag + clean subject + relative time
 *  (no gitmoji); a stable `last-commit` seam whether or not a commit exists. */
function LastCommit({ lastCommit }: { lastCommit: WorkspaceActivityView["recent_commits"][number] | null }) {
  if (!lastCommit) {
    return (
      <span data-testid="last-commit" className="italic text-muted-foreground/70">
        no commits yet
      </span>
    );
  }
  const { tag, subject } = parseCommitSubject(lastCommit.subject);
  return (
    <span data-testid="last-commit" className="flex min-w-0 items-baseline gap-1">
      {tag && <span className="shrink-0 font-mono text-muted-foreground">{tag}</span>}
      <span className="min-w-0 truncate" title={lastCommit.subject}>
        {subject}
      </span>
      <span className="shrink-0 whitespace-nowrap text-[11px]">
        <RelativeTime iso={lastCommit.committed_at} />
      </span>
    </span>
  );
}
