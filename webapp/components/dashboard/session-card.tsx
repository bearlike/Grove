import Link from "next/link";
import { GitCommitHorizontal, Radio, Zap } from "lucide-react";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "@/components/workspace/status-badge";
import { PlacementBadge } from "@/components/workspace/placement-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { AgentBadge } from "@/components/dashboard/running-indicator";
import { AgentGlyph } from "@/lib/grove/agent-icon";
import { tierForActivity } from "@/lib/grove/activity-tier";
import { cn } from "@/lib/utils";
import { agentStateGlyph, agentStateLabel } from "@/lib/grove/agent-state-tokens";
import type { CommitSummaryView, WorkspaceActivityView } from "@/lib/grove/types";

/**
 * One Activity-Dashboard tile. The card now leads with WHAT WAS DONE — the last
 * commit (`recent_commits[0]`) is the durable "what + when committed" line, with
 * up to two more recent commits below for at-a-glance worktree history. The live
 * ephemeral "what it's doing right now" (`current_task`) is a SECONDARY line shown
 * only while the agent is in the active tier. The animated running indicator is the
 * brand `AgentBadge` next to the title (not a static glyph). The whole card takes
 * the activity-tier treatment — dormant cards dim, attention cards stay full-opacity
 * and gain a highlight ring — so transparency itself signals the active tier.
 *
 * Test seam: `data-testid="session-card"` + `data-agent-state` + `data-tier`,
 * `data-testid="agent-state-label"`, `data-testid="last-commit"`,
 * `data-testid="current-task"` (active-only), `data-testid="agent-badge"`,
 * `data-testid="live-toggle"`, and the title link.
 */
export function SessionCard({
  activity,
  liveOpen = false,
  onToggleLive,
}: {
  activity: WorkspaceActivityView;
  liveOpen?: boolean;
  onToggleLive?: (id: string) => void;
}) {
  const s = activity.state;
  const primary = activity.sessions[0]?.activity ?? null;
  const adapterKind = activity.sessions[0]?.session.adapter_kind;
  const agentState = primary?.state ?? "unknown";
  const color = `var(--agent-${agentState}, var(--agent-unknown))`;
  const { tier, opacityClass, accentVar, treatment } = tierForActivity(primary?.state ?? null, s.status);

  const lastCommit = activity.recent_commits[0] ?? null;
  const olderCommits = activity.recent_commits.slice(1, 3);
  // Self-summary contract: `interpreted_status ?? title ?? current_task`. The
  // first two are durable (a session keeps its name while idle), so they show
  // on any tier; only `current_task` below stays active-gated (ephemeral).
  const sessionTitle = primary ? primary.interpreted_status ?? primary.title : null;
  // The live "doing now" line is meaningful only while the agent is active.
  const ongoing = tier === "active" ? primary?.current_task ?? null : null;
  const extraSessions = activity.sessions.slice(1);
  const canGoLive = agentState === "working" && onToggleLive != null;
  const hasBase = activity.base_ahead > 0 || activity.base_behind > 0;
  const hasTokens = primary != null && (primary.tokens_in > 0 || primary.tokens_out > 0);

  return (
    <Card
      data-testid="session-card"
      data-agent-state={agentState}
      data-tier={tier}
      className={cn(
        "flex h-full flex-col gap-3 p-4 transition-[box-shadow,border-color,opacity] duration-200 hover:shadow-md",
        opacityClass,
        // Attention (waiting/blocked/error) is highlighted with a ring, never dimmed.
        treatment === "ring" && "ring-2 ring-offset-0",
        liveOpen && "ring-2 ring-ring",
      )}
      style={treatment === "ring" && !liveOpen ? { borderColor: accentVar, ["--tw-ring-color" as string]: accentVar } : undefined}
    >
      {/* Header: brand running-indicator · title / branch · agent · model — status · placement · age */}
      <div className="flex items-start gap-2.5">
        <AgentBadge
          agentName={s.agent_name}
          adapterKind={adapterKind}
          primaryState={primary?.state ?? null}
          workspaceStatus={s.status}
          className="mt-0.5 shrink-0"
        />
        <div className="min-w-0 flex-1">
          <Link
            href={`/w/${encodeURIComponent(s.id)}`}
            title={s.title}
            className="block truncate text-base font-semibold leading-tight hover:underline focus-visible:underline focus-visible:outline-none"
          >
            {s.title}
          </Link>
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-xs">
            <span className="truncate font-mono text-[var(--ref-branch)]" title={s.branch}>
              {s.branch}
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="font-mono text-[var(--ref-info)]">{s.agent_name}</span>
            {primary?.model && (
              <>
                <span className="text-muted-foreground">·</span>
                <span className="truncate font-mono text-muted-foreground">{primary.model}</span>
              </>
            )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <div className="flex items-center gap-1.5">
            <PlacementBadge placement={s.placement} />
            <StatusBadge status={s.status} size="sm" />
          </div>
          {/* Per-card "updated Xs ago" — when this card was last produced. */}
          <span className="text-[11px] text-muted-foreground" data-testid="observed-at">
            updated <RelativeTime iso={activity.observed_at} />
          </span>
        </div>
      </div>

      {/* Latest committed activity — the durable "what was done, when". */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <span className="inline-flex items-center gap-1 text-sm font-medium" style={{ color }}>
            <span data-testid="agent-state-label">{agentStateLabel(agentState)}</span>
          </span>
          {canGoLive && (
            <button
              type="button"
              data-testid="live-toggle"
              aria-pressed={liveOpen}
              onClick={() => onToggleLive?.(s.id)}
              className={cn(
                "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
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

        {/* Why the agent stopped — shown only when the state IS error and the
            adapter surfaced a reason. Mono + error tone, clamped to two lines. */}
        {agentState === "error" && primary?.error_detail && (
          <p
            data-testid="error-detail"
            className="line-clamp-2 font-mono text-xs"
            style={{ color: "var(--agent-error)" }}
            title={primary.error_detail}
          >
            {primary.error_detail}
          </p>
        )}

        {/* Durable session self-summary — the agent's own name for this run. */}
        {sessionTitle && (
          <p
            data-testid="session-title"
            className="line-clamp-1 text-xs text-muted-foreground"
            title={sessionTitle}
          >
            {sessionTitle}
          </p>
        )}

        {/* Primary activity line = the LAST COMMIT (what was done, and when). */}
        {lastCommit ? (
          <CommitLine commit={lastCommit} primary data-testid="last-commit" />
        ) : (
          <p className="text-sm italic text-muted-foreground/70" data-testid="last-commit">
            no commits yet
          </p>
        )}

        {/* Secondary, active-only "ongoing action" — the ephemeral live signal,
            visually distinct (Zap, accent tint) from the durable commit line. */}
        {ongoing && (
          <p
            data-testid="current-task"
            className="flex items-start gap-1.5 text-xs"
            style={{ color }}
          >
            <Zap aria-hidden className="mt-0.5 size-3 shrink-0" />
            <span className="line-clamp-2">{ongoing}</span>
          </p>
        )}

        {/* Tight commit history — the next two commits, precise to the worktree. */}
        {olderCommits.length > 0 && (
          <ul className="flex flex-col gap-0.5" data-testid="commit-history">
            {olderCommits.map((c) => (
              <li key={c.sha}>
                <CommitLine commit={c} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Labeled metric grid — reads as structure, not a cramped string */}
      <dl className="mt-auto grid grid-cols-2 gap-x-4 gap-y-2 border-t border-border pt-3 sm:grid-cols-4">
        <Stat label="Changes">
          <span className="text-[var(--ref-add)]">+{activity.diff_added}</span>{" "}
          <span className="text-[var(--ref-remove)]">−{activity.diff_removed}</span>
        </Stat>
        <Stat label="Base">
          {hasBase ? `↑${activity.base_ahead} ↓${activity.base_behind}` : "—"}
        </Stat>
        <Stat label="Turns">
          {primary
            ? `${primary.human_turns}t ${primary.assistant_replies}r ${primary.tool_calls}⚒`
            : "—"}
        </Stat>
        <Stat label="Tokens">
          {hasTokens
            ? `${humanTokens(primary!.tokens_in)}↑ ${humanTokens(primary!.tokens_out)}↓`
            : "—"}
        </Stat>
      </dl>

      {/* Multi-session footer — the wall renders the primary session only;
          the rest get a glyph-per-session strip linking to the detail page. */}
      {extraSessions.length > 0 && (
        <Link
          href={`/w/${encodeURIComponent(s.id)}`}
          data-testid="extra-sessions"
          className="flex items-center gap-2 border-t border-border pt-2 text-[11px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <span className="flex items-center gap-1.5">
            {extraSessions.map((sa) => (
              <span
                key={sa.session.session_id}
                className="inline-flex items-center gap-0.5"
                title={`${sa.session.adapter_kind}: ${agentStateLabel(sa.activity.state)}`}
              >
                <span
                  aria-hidden
                  style={{ color: `var(--agent-${sa.activity.state}, var(--agent-unknown))` }}
                >
                  {agentStateGlyph(sa.activity.state)}
                </span>
                <AgentGlyph
                  agentName={sa.session.adapter_kind}
                  adapterKind={sa.session.adapter_kind}
                  className="size-3"
                />
              </span>
            ))}
          </span>
          +{extraSessions.length} more session{extraSessions.length > 1 ? "s" : ""}
        </Link>
      )}
    </Card>
  );
}

/**
 * One commit row: `<GitCommitHorizontal/> sha subject · committed <relative>`.
 * `primary` makes the subject readable body text (the headline last-commit);
 * the compact history rows stay muted so the headline still leads.
 */
function CommitLine({
  commit,
  primary = false,
  ...rest
}: {
  commit: CommitSummaryView;
  primary?: boolean;
  "data-testid"?: string;
}) {
  return (
    <div
      {...rest}
      className={cn(
        "flex min-w-0 items-baseline gap-1.5",
        primary ? "text-sm" : "text-xs text-muted-foreground",
      )}
    >
      <GitCommitHorizontal
        aria-hidden
        className={cn("shrink-0 self-center", primary ? "size-4" : "size-3")}
      />
      <span className="shrink-0 font-mono text-[var(--ref-branch)]">{shortSha(commit.sha)}</span>
      <span className={cn("truncate font-mono", primary && "text-foreground")} title={commit.subject}>
        {commit.subject}
      </span>
      <span className="ml-auto shrink-0 whitespace-nowrap text-[11px] text-muted-foreground">
        <RelativeTime iso={commit.committed_at} />
      </span>
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">{label}</dt>
      <dd className="truncate font-mono text-xs tabular-nums">{children}</dd>
    </div>
  );
}

function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function humanTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}
