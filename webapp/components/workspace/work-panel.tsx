"use client";

import { useEffect, useState } from "react";
import { Maximize2, Minimize2, GitCompare, Info, SquareTerminal, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { TerminalPane } from "@/components/terminal/terminal-pane";
import { StatTrio } from "@/components/workspace/stat-trio";
import { CommitList } from "@/components/workspace/commit-list";
import { PlacementBadge } from "@/components/workspace/placement-badge";
import { RelativeTime } from "@/components/shared/relative-time";
import { cn } from "@/lib/utils";
import type { AgentLiveStatus } from "@/lib/grove/agent-activity";
import type { CommitSummaryView, WorkspacePeekView } from "@/lib/grove/types";

const SECTION_LABEL =
  "text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground";

type PanelTab = "terminal" | "diff" | "info";

/**
 * The session page's work panel (ADE #142) — the tabbed surface that fills
 * `AgentWorkspace`'s right pane, replacing the once-bare `TerminalPane`. Three
 * tabs, wire-data only:
 *
 * - **Terminal** — the existing `TerminalPane` machinery, verbatim (this is
 *   still Grove's D2 differentiator — a real tmux pane, not a builder-style
 *   activity log). Its tab carries a permanent live-pulse dot, the same
 *   convention `ViewSwitcher`'s old terminal tab and the pane's own capture
 *   badge already use — one glyph language, not a second "unread" heuristic.
 * - **Diff** — ahead/behind/dirty (`StatTrio`) + the ± line-changed summary +
 *   the full `CommitList`. Exactly the "Changes"/"Commits" sections the old
 *   `SessionIdentity` popover held, re-homed verbatim; still wire-data only
 *   (no hunk view — the daemon doesn't expose per-file diffs).
 * - **Info** — the metrics one-liner (turns · tools · tokens, `metrics` testid
 *   — the SAME seam the dashboard card uses, so a drift test can't miss it)
 *   plus subagent count, agent + model identity, placement, and created/paused
 *   timestamps. Deliberately omits `worktree_path` — a host filesystem path is
 *   host-private-ish and was never rendered anywhere in the old UI either.
 *
 * Tab selection is page-session-local `useState` (design ruling: "smallest
 * seam, don't add a store slice") — it resets on navigation, which is exactly
 * right for a per-visit UI preference. Full-screen is a plain CSS overlay
 * (`fixed inset-0`), not a portal/Dialog, so it composes with the resizable
 * split underneath without fighting focus-trap semantics.
 *
 * Test seams: `work-panel` (root), `work-panel-tab-terminal` / `-diff` / `-info`,
 * `work-panel-fullscreen`. The Terminal tab keeps every seam `TerminalPane`
 * already owns (`terminal-pane`, `terminal-capture-badge`, `peek-snapshot`).
 */
export function WorkPanel({
  peek,
  live,
  commits,
  commitsLoading,
  className,
}: {
  peek: WorkspacePeekView;
  live: AgentLiveStatus;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
  className?: string;
}) {
  const [tab, setTab] = useState<PanelTab>("terminal");
  const [fullscreen, setFullscreen] = useState(false);

  // Escape exits full screen — the standard overlay convention; harmless when
  // not full screen (the listener just never sees a reason to fire).
  useEffect(() => {
    if (!fullscreen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setFullscreen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [fullscreen]);

  return (
    <div
      data-testid="work-panel"
      className={cn(
        "flex min-h-0 min-w-0 flex-1 flex-col bg-background",
        fullscreen && "fixed inset-0 z-40",
        className,
      )}
    >
      <div className="flex shrink-0 items-center gap-0.5 bg-muted/40 px-2 py-1">
        <div role="tablist" aria-label="Work panel" className="flex items-center gap-0.5">
          <PanelTabButton
            testid="work-panel-tab-terminal"
            icon={SquareTerminal}
            label="Terminal"
            live
            selected={tab === "terminal"}
            onClick={() => setTab("terminal")}
          />
          <PanelTabButton
            testid="work-panel-tab-diff"
            icon={GitCompare}
            label="Diff"
            selected={tab === "diff"}
            onClick={() => setTab("diff")}
          />
          <PanelTabButton
            testid="work-panel-tab-info"
            icon={Info}
            label="Info"
            selected={tab === "info"}
            onClick={() => setTab("info")}
          />
        </div>
        <button
          type="button"
          data-testid="work-panel-fullscreen"
          aria-label={fullscreen ? "Exit full screen" : "Full screen"}
          aria-pressed={fullscreen}
          onClick={() => setFullscreen((v) => !v)}
          className={cn(
            "ml-auto inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
          )}
        >
          {fullscreen ? <Minimize2 className="size-3.5" /> : <Maximize2 className="size-3.5" />}
        </button>
      </div>

      {/* Only the active tab mounts (same conditional-mount contract the outer
          transcript/terminal single-pane view already uses) — the Terminal tab
          is stateless over its `peek` prop, so remounting on tab return costs
          nothing. */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {tab === "terminal" && (
          <TerminalPane
            snapshot={peek.agent_snapshot}
            takenAt={peek.snapshot_taken_at}
            target={peek.state.tmux_session}
          />
        )}
        {tab === "diff" && <DiffTab peek={peek} commits={commits} commitsLoading={commitsLoading} />}
        {tab === "info" && <InfoTab peek={peek} live={live} />}
      </div>
    </div>
  );
}

function DiffTab({
  peek,
  commits,
  commitsLoading,
}: {
  peek: WorkspacePeekView;
  commits: CommitSummaryView[] | undefined;
  commitsLoading?: boolean;
}) {
  const changed = peek.diff_added > 0 || peek.diff_removed > 0;
  return (
    <div data-testid="work-panel-diff-content" className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <StatTrio ahead={peek.base_ahead} behind={peek.base_behind} dirty={peek.dirty_files} />
      <p className="text-xs text-muted-foreground">
        {changed ? (
          <>
            <span className="font-medium text-[var(--ref-add)]">+{peek.diff_added}</span>
            {" / "}
            <span className="font-medium text-[var(--ref-remove)]">−{peek.diff_removed}</span>
            {" lines changed"}
          </>
        ) : (
          "Working tree clean."
        )}
      </p>
      <div className="min-h-0 flex-1">
        <CommitList commits={commits} isLoading={commitsLoading} />
      </div>
    </div>
  );
}

function InfoTab({ peek, live }: { peek: WorkspacePeekView; live: AgentLiveStatus }) {
  const s = peek.state;
  return (
    <div data-testid="work-panel-info-content" className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4">
      <section className="space-y-1.5">
        <p className={SECTION_LABEL}>Activity</p>
        <p data-testid="metrics" className="font-mono text-xs tabular-nums text-muted-foreground">
          {live.metricsLine ?? "—"}
        </p>
        {live.subagents > 0 && (
          <p className="text-xs text-muted-foreground">
            {live.subagents} bg agent{live.subagents > 1 ? "s" : ""}
          </p>
        )}
      </section>

      <section className="space-y-1.5">
        <p className={SECTION_LABEL}>Identity</p>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="border-[var(--ref-info)]/40 font-mono text-[var(--ref-info)]">
            {s.agent_name}
          </Badge>
          {live.model && (
            <Badge variant="outline" className="font-mono">
              {live.model}
            </Badge>
          )}
          <PlacementBadge placement={s.placement} size="sm" />
        </div>
      </section>

      <section className="space-y-1 text-xs text-muted-foreground">
        <p className={SECTION_LABEL}>Timeline</p>
        <p>
          Created <RelativeTime iso={s.created_at} />
        </p>
        {s.paused_at && (
          <p>
            Paused <RelativeTime iso={s.paused_at} />
          </p>
        )}
      </section>
    </div>
  );
}

/** A quiet internal tab, same visual language as `ViewSwitcher`'s TabButton
 *  (text at `sm`+, icon-only below it) so the panel's own tab bar doesn't
 *  introduce a second tab idiom. */
function PanelTabButton({
  selected,
  onClick,
  testid,
  icon: Icon,
  label,
  live,
}: {
  selected: boolean;
  onClick: () => void;
  testid: string;
  icon: LucideIcon;
  label: string;
  live?: boolean;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      aria-label={label}
      data-testid={testid}
      onClick={onClick}
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-md px-2 text-xs transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        selected ? "bg-accent font-medium text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      <Icon aria-hidden className="size-3.5 shrink-0 sm:hidden" />
      <span className="hidden sm:inline">{label}</span>
      {live && (
        <span
          aria-hidden
          className="size-1.5 shrink-0 rounded-full bg-[var(--status-active)] motion-safe:animate-pulse"
        />
      )}
    </button>
  );
}
