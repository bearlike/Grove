"use client";
import { useState } from "react";
import Link from "next/link";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentGlyph } from "@/lib/grove/agent-icon";
import { agentStateGlyph, agentStateLabel } from "@/lib/grove/agent-state-tokens";
import { RelativeTime } from "@/components/shared/relative-time";
import { TurnsView } from "@/components/workspace/turns-view";
import { humanTokens } from "@/lib/grove/format";
import { useProjectSessions } from "@/lib/grove/hooks";
import type { SessionSummaryView } from "@/lib/grove/types";

/**
 * The home page's per-project Sessions section — every recorded agent session
 * across ALL of one repo's worktrees (newest-first, as the wire delivers
 * them), Grove-managed and hand-started alike. Collapsed by default to one
 * compact header row so the workspace grid never moves; expanding mounts the
 * body, and mounting IS the fetch trigger (same on-expand tier as TurnsView),
 * so a collapsed section costs zero requests.
 *
 * Grove-managed rows carry workspace attribution (title + teal mono branch,
 * linking to `/w/{id}`) and expand inline into their conversation digest;
 * hand-staged rows have no owning workspace, so they show their raw
 * `git_branch` and offer no drill-down (the turns endpoint is
 * workspace-scoped) — attribution presence is itself the Grove signal.
 *
 * Test seam: `data-testid="project-sessions"`, `"project-sessions-toggle"`,
 * `"session-row"` + `data-session-id`, `"session-workspace-link"`,
 * `"session-provenance"`, and the "no recorded sessions" empty-state text.
 */
export function ProjectSessions({ repoRoot }: { repoRoot: string }) {
  const [open, setOpen] = useState(false);
  const Chevron = open ? ChevronDown : ChevronRight;

  return (
    <section
      data-testid="project-sessions"
      className="rounded-lg border border-border bg-card"
    >
      <button
        type="button"
        data-testid="project-sessions-toggle"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-baseline gap-2 rounded-lg px-3 py-2 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <Chevron aria-hidden className="size-3.5 shrink-0 self-center text-muted-foreground" />
        <span className="shrink-0 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          Sessions
        </span>
        <span className="min-w-0 truncate text-xs text-muted-foreground/70">
          agent sessions across this project&apos;s worktrees — Grove-managed and
          hand-started
        </span>
      </button>
      {open && <ProjectSessionsBody repoRoot={repoRoot} />}
    </section>
  );
}

function ProjectSessionsBody({ repoRoot }: { repoRoot: string }) {
  const { data, isLoading, isError } = useProjectSessions(repoRoot);
  // One row expanded at a time, section-local state: the digest is an inline
  // drill-down, not a navigation, so no new route.
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isError) {
    return (
      <p className="border-t border-border px-3 py-2.5 text-sm text-muted-foreground">
        couldn&apos;t load sessions
      </p>
    );
  }
  if (isLoading || !data) {
    return (
      <div className="flex flex-col gap-2 border-t border-border px-3 py-3">
        <Skeleton className="h-5 w-full" />
        <Skeleton className="h-5 w-5/6" />
        <Skeleton className="h-5 w-2/3" />
      </div>
    );
  }
  if (data.length === 0) {
    return (
      <p className="border-t border-border px-3 py-2.5 text-sm text-muted-foreground">
        no recorded sessions in this project
      </p>
    );
  }

  return (
    <ul className="border-t border-border px-3">
      {data.map((s) => (
        <SessionRow
          key={s.session_id}
          session={s}
          expanded={expandedId === s.session_id}
          onToggle={() =>
            setExpandedId((cur) => (cur === s.session_id ? null : s.session_id))
          }
        />
      ))}
    </ul>
  );
}

function SessionRow({
  session: s,
  expanded,
  onToggle,
}: {
  session: SessionSummaryView;
  expanded: boolean;
  onToggle: () => void;
}) {
  const state = s.activity.state;
  // CSS-var fallback rule: a streamed state the client's enum predates would
  // otherwise resolve to no var at all — fall back to the neutral unknown tone.
  const stateColor = `var(--agent-${state}, var(--agent-unknown))`;
  // The turns endpoint is workspace-scoped, so only workspace-attributed rows
  // can drill down — a hand-staged row renders without the (dead) chevron.
  const drillable = Boolean(s.workspace_id);
  const Chevron = expanded ? ChevronDown : ChevronRight;

  const summary = (
    <>
      <AgentGlyph
        agentName={s.adapter_kind}
        adapterKind={s.adapter_kind}
        className="size-4 shrink-0"
      />
      <div className="min-w-0 flex-1">
        <p className="line-clamp-1 text-sm text-foreground">
          {s.title ?? s.first_prompt ?? s.session_id}
        </p>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 font-mono text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1" style={{ color: stateColor }}>
            <span aria-hidden>{agentStateGlyph(state)}</span>
            {agentStateLabel(state)}
          </span>
          <span>
            {s.activity.human_turns}t {s.activity.tool_calls}⚒
          </span>
          <span>
            {humanTokens(s.activity.tokens_in)}↑ {humanTokens(s.activity.tokens_out)}↓
          </span>
          {s.activity.model && <span className="truncate">{s.activity.model}</span>}
        </p>
      </div>
    </>
  );

  return (
    <li
      className="border-b border-border last:border-b-0"
      data-testid="session-row"
      data-session-id={s.session_id}
    >
      <div className="flex items-center gap-2.5 py-2.5">
        {drillable ? (
          <button
            type="button"
            aria-expanded={expanded}
            onClick={onToggle}
            className="flex min-w-0 flex-1 items-center gap-2.5 text-left transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <Chevron aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
            {summary}
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2.5">
            {/* Chevron-width spacer keeps glyphs and text columns aligned
                across drillable and non-drillable rows. */}
            <span aria-hidden className="size-3.5 shrink-0" />
            {summary}
          </div>
        )}
        {s.workspace_id ? (
          <Link
            href={`/w/${encodeURIComponent(s.workspace_id)}`}
            data-testid="session-workspace-link"
            className="-mx-1 flex max-w-[8rem] shrink-0 flex-col items-end rounded-sm px-1 py-0.5 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:max-w-[12rem]"
          >
            <span className="max-w-full truncate text-[11px] text-muted-foreground">
              {s.workspace_title}
            </span>
            <span className="max-w-full truncate font-mono text-xs text-[var(--ref-branch)]">
              {s.workspace_branch}
            </span>
          </Link>
        ) : (
          s.git_branch && (
            <span className="max-w-[8rem] shrink-0 truncate font-mono text-xs text-[var(--ref-branch)] sm:max-w-[12rem]">
              {s.git_branch}
            </span>
          )
        )}
        <span
          data-testid="session-provenance"
          className="shrink-0 font-mono text-[10px] uppercase tracking-wider text-muted-foreground/70"
        >
          {provenanceLabel(s.provenance)}
        </span>
        <span className="shrink-0 text-[11px] text-muted-foreground">
          <RelativeTime iso={s.modified_at} />
        </span>
      </div>
      {expanded && s.workspace_id && (
        <div className="pl-6">
          <TurnsView workspaceId={s.workspace_id} sessionId={s.session_id} />
        </div>
      )}
    </li>
  );
}

/** Quiet human labels for the wire's provenance codes; unknown codes pass raw. */
function provenanceLabel(p: string): string {
  if (p === "grove_launched") return "grove";
  if (p === "fs_discovered" || p === "hook_discovered") return "hand-started";
  return p;
}
