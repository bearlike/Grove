"use client";
import Link from "next/link";
import {
  ArrowDown,
  ArrowDownToLine,
  ArrowUp,
  ArrowUpFromLine,
  FileDiff,
  GitBranch,
  ListChecks,
  MessagesSquare,
  Radio,
  Wrench,
} from "lucide-react";
import { Card } from "@/components/ui/card";
import { StatusBadge } from "./status-badge";
import { PlacementBadge } from "./placement-badge";
import { PhaseBadge } from "./phase-badge";
import { ProvisionLine } from "./provision-progress";
import { TicketLinkage } from "./ticket-refs";
import { RelativeTime } from "@/components/shared/relative-time";
import { Stat } from "@/components/shared/stat";
import { AgentStateMark } from "@/components/shared/state-mark";
import { RuntimeMark } from "@/components/shared/runtime-mark";
import { LandingRing } from "@/components/shared/landing-ring";
import { AgentLiveStatus } from "@/lib/grove/agent-activity";
import { tierForActivity } from "@/lib/grove/activity-tier";
import { parseCommitSubject } from "@/lib/grove/commit-format";
import { humanTokens } from "@/lib/grove/format";
import { cn } from "@/lib/utils";
import type { WorkspaceActivityView } from "@/lib/grove/types";

/**
 * The ONE workspace card. Its metadata reads through a single `Stat`
 * grammar — no icons-vs-no-icons drift, zeros suppressed rather than shown
 * as noise. This card speaks the same calm `state mark · title · time`
 * vocabulary as the session rail row across three zones separated by space,
 * not lines or a tinted well:
 *
 *   HEADER   — the canonical `AgentStateMark` glyph (the rail's own state atom,
 *     not a second badge) · title link (ONE line, truncate) · a right-aligned
 *     relative time. The lifecycle `StatusBadge` returns to this line ONLY when
 *     it is itself the signal (no agent session, or a broken orphaned/error
 *     lifecycle) — every healthy card wears zero pills.
 *   TICKETS  — `TicketLinkage`: issue(s) → PR, mounted once ANY ref exists
 *     (an issue-only workspace is most of a workspace's life, and still gets
 *     linkage here). Placed right under the header, next to the `PhaseBadge`
 *     it usually correlates with — a phase reaching delivering/done is what a
 *     PR existing already implies, so this row names the OUTCOME once instead
 *     of repeating it.
 *   CONTEXT  — "happening now" (`AgentLiveStatus.taskLine`), `line-clamp-1`;
 *     error detail wins the slot while erroring, keeping the `· N bg` suffix.
 *   META     — TWO aligned `Stat` rows on one 11px baseline grid (`h-5` line
 *     boxes so card heights stay uniform across the grid), then the one prose
 *     last-commit line. Row 1 = provenance (branch · ahead · behind · dirty ·
 *     placement-when-root); row 2 = activity (turns · tool calls · tokens in ·
 *     tokens out) with the WORKING-gated Live toggle pinned right. Every stat is
 *     an icon + tabular value via `Stat` — the unit lives in the tooltip, a zero
 *     renders NOTHING (no "0 ahead" noise). No footer well: tone breaks belong to
 *     the page, not every card.
 *
 *     The tokens-in/out slot is itself live: while `AgentLiveStatus
 *     .isGenerating` is true (a fast side-channel is actively reporting —
 *     `live` on the wire, otherwise `None`/absent) it swaps to a pulsing
 *     `live-token-flow` readout of the IN-FLIGHT counts, settling back to the
 *     cumulative `Stat`s the moment generation stops. Never both at once, so
 *     the row never grows or jitters.
 *
 * Quiet-chrome discipline (design-system.md): the surface is `rounded-xl bg-card`
 * on a `border-border/60` hairline; hover is a tonal shift + border-strengthen —
 * NO shadow, NO translate lift, NO full-card status glow. Attention
 * (waiting/blocked/error) keeps a single thin LEFT `border-l-2` accent bar in the
 * tier accent var — the only on-card hue. Live focus is a quiet terracotta ring.
 *
 * Zero-loss: every datum the card carries keeps a visible home (a zero is
 * suppressed, not lost — its stat reappears the instant it goes nonzero).
 *
 * Test seams kept stable: `data-testid="workspace-card"` + `data-status` +
 * `data-agent-state` + `data-tier`; the `state-mark` glyph, `happening-now`,
 * `last-commit`, `live-toggle`, `placement-badge`/`status-badge` atoms, the title
 * `<Link>`, and each `Stat`'s `data-testid="stat"` + `data-stat="<label>"`.
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
  const phase = activity.phase ?? null;
  const todoProgress = activity.todo ?? null;
  const ticketRefs = s.ticket_refs ?? [];
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
  // A container being built has no agent session and nothing to say on the
  // agent axis — the lifecycle status IS the whole story for the length of the
  // build, so the card swaps its "happening now" line for the live provision
  // readout rather than reporting "no agent session" for six minutes.
  const provisioning = s.status === "provisioning";
  // Lifecycle badge only where it is itself the signal: no agent session to badge
  // on the agent axis, or a broken lifecycle state worth surfacing over it.
  const showStatus = primary == null || s.status === "orphaned" || s.status === "error";
  // Attention (waiting/blocked/error) earns a single thin left accent bar — the
  // only on-card hue, never a full ring/fill (design quiet-chrome rule).
  const attention = treatment === "ring";

  return (
    <Card
      data-testid="workspace-card"
      data-status={s.status}
      data-agent-state={agentState}
      data-tier={tier}
      className={cn(
        "group relative flex h-full flex-col gap-2 overflow-hidden rounded-xl border-border/60 bg-card p-3.5 transition-colors",
        "hover:border-border hover:bg-muted/40",
        attention && "border-l-2",
        liveOpen && "ring-2 ring-ring",
      )}
      style={attention ? { borderLeftColor: accentVar } : undefined}
    >
      {/* ── HEADER: state · title · time ───────────────────────────────────── */}
      <div className="flex items-center gap-2">
        <AgentStateMark state={agentState} className="shrink-0 text-[13px]" />
        <Link
          href={`/w/${encodeURIComponent(s.id)}`}
          title={`${s.title} — ${s.branch}`}
          className="min-w-0 flex-1 truncate text-sm font-medium leading-snug text-foreground hover:underline focus-visible:underline focus-visible:outline-none"
        >
          {s.title}
        </Link>
        <PhaseBadge phase={phase} />
        {showStatus && <StatusBadge status={s.status} size="sm" className="shrink-0" />}
        <span className="shrink-0 whitespace-nowrap text-xs text-muted-foreground">
          <RelativeTime iso={s.updated_at} />
        </span>
      </div>

      {/* ── TICKETS: issue(s) → PR, only when a PR exists (see class doc) ───── */}
      <TicketLinkage refs={ticketRefs} />

      {/* ── CONTEXT: what the agent is doing now (one line) ─────────────────── */}
      {provisioning ? (
        <ProvisionLine workspaceId={s.id} startedAt={s.provision_started_at} />
      ) : live.errorDetail ? (
        <p
          data-testid="happening-now"
          className="line-clamp-1 text-[13px] leading-snug"
          style={{ color: "var(--agent-error)" }}
          title={live.errorDetail}
        >
          {live.errorDetail}
        </p>
      ) : (
        <p
          data-testid="happening-now"
          className={cn(
            "line-clamp-1 text-[13px] leading-snug",
            happening ? "text-muted-foreground" : "italic text-muted-foreground/70",
          )}
          title={happening ?? undefined}
        >
          {happening ?? (primary ? "no activity yet" : "no agent session")}
          {subagents > 0 && (
            <span className="text-muted-foreground/70">
              {" · "}
              {subagents} bg agent{subagents > 1 ? "s" : ""}
            </span>
          )}
        </p>
      )}

      {/* ── META: two aligned Stat rows on one 11px baseline grid, then the
          prose last-commit line. `h-5` line boxes + shared `gap-3` keep the
          rhythm and card heights uniform across the grid; zeros self-suppress. */}
      <div className="mt-auto flex flex-col gap-1 pt-0.5 text-[11px]">
        {/* Row 1 — provenance: branch, then the git deltas. */}
        <div className="flex h-5 items-center gap-3">
          <span className="flex min-w-0 items-center gap-1 text-[var(--ref-branch)]">
            <GitBranch aria-hidden className="size-3 shrink-0" />
            <span title={s.branch} className="truncate font-mono">
              {s.branch}
            </span>
          </span>
          <Stat icon={ArrowUp} value={activity.base_ahead} label="ahead" tone="add" />
          <Stat icon={ArrowDown} value={activity.base_behind} label="behind" tone="remove" />
          <Stat icon={FileDiff} value={activity.dirty_files} label="dirty" />
          {/* The two "where does this run" qualifiers, pinned right and read as
              one group. The runtime mark is ALWAYS here (both host and
              container carry one — the isolation boundary has no silent
              state); the placement badge stays silent for the worktree
              default, so the group is one mark or two, never zero. */}
          <span className="ml-auto flex shrink-0 items-center gap-1.5">
            <RuntimeMark runtime={s.runtime} />
            <PlacementBadge placement={s.placement} size="sm" />
          </span>
        </div>
        {/* Row 2 — activity: turns, tool calls, tokens; Live toggle pinned right.
            Tokens in/out are the settled cumulative totals UNLESS a live tier
            is actively reporting, in which case the live in-flight counts take
            that same slot — never both at once, so the row never grows.
            `live.isGenerating` is `false` whenever no fast side-channel is
            wired, so this reads exactly like the cumulative-only card until
            one lands. */}
        <div className="flex h-5 items-center gap-3">
          <Stat icon={MessagesSquare} value={live.turns} label="turns" />
          <Stat icon={Wrench} value={live.toolCalls} label="tool calls" />
          {todoProgress && todoProgress.total > 0 && (
            <Stat
              icon={ListChecks}
              value={`${todoProgress.completed}/${todoProgress.total}`}
              label="todos done"
            />
          )}
          {live.isGenerating ? (
            <span
              data-testid="live-token-flow"
              title={`generating — ${live.liveTokensIn ?? 0} in / ${live.liveTokensOut ?? 0} out`}
              className="inline-flex items-center gap-1 whitespace-nowrap tabular-nums text-[var(--agent-working)]"
            >
              <span
                aria-hidden
                className="size-1.5 shrink-0 animate-grove-pulse rounded-full bg-[var(--agent-working)] motion-reduce:animate-none"
              />
              <ArrowDownToLine aria-hidden className="size-3 shrink-0" />
              {humanTokens(live.liveTokensIn ?? 0)}
              <ArrowUpFromLine aria-hidden className="size-3 shrink-0" />
              {humanTokens(live.liveTokensOut ?? 0)}
            </span>
          ) : (
            <>
              <Stat icon={ArrowDownToLine} value={live.tokensIn} label="tokens in" />
              <Stat icon={ArrowUpFromLine} value={live.tokensOut} label="tokens out" />
            </>
          )}
          {canGoLive && (
            <button
              type="button"
              data-testid="live-toggle"
              aria-pressed={liveOpen}
              onClick={() => onToggleLive?.(s.id)}
              className={cn(
                "ml-auto inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
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
        {/* The one prose line — not a stat, so it lives outside the grammar. */}
        <LastCommit lastCommit={lastCommit} />
      </div>

      {/* One-shot terracotta ring the moment a just-created workspace lands in
          the grid — decays to nothing (see LandingRing). */}
      <LandingRing landedAt={s.created_at} />
    </Card>
  );
}

/** The meta row's last-commit slot — parsed tag + clean subject + relative time
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
