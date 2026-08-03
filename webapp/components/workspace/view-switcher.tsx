"use client";

import { type LucideIcon, Columns2, MessagesSquare, Square, SquareTerminal } from "lucide-react";
import { cn } from "@/lib/utils";

/** The active agent pane in a single-pane view. */
export type AgentTab = "transcript" | "terminal";
/** Whether the agent surface shows one pane at a time or a side-by-side split. */
export type AgentView = "tabs" | "split";

/**
 * The session page's pane view selector — the ONLY tab UI on the page. Two
 * quiet text tabs pick which pane fills a single-pane view; the lg-only icon
 * pair toggles single-pane vs. side-by-side split. This is plain controlled
 * buttons with a hand-rolled `tablist`/`tab`/`aria-selected` contract (not
 * radix `Tabs`), so it rides the header identity cluster with no `TabsList`
 * pill chrome — separation by tone + space, not a drawn line.
 *
 * In a split both panes render at once, so no single text tab reads as
 * "selected"; clicking one drops to single-pane focus on that pane. The
 * Terminal tab keeps its live dot — the always-fresh cue.
 *
 * Test seams: `view-switcher` (wrapper), `tab-transcript`, `tab-terminal`,
 * `view-tabs`, `view-split`.
 */
export function ViewSwitcher({
  tab,
  view,
  isLg,
  onTabChange,
  onViewChange,
}: {
  tab: AgentTab | null;
  view: AgentView;
  /** Split is an lg-only affordance; below lg the toggle is hidden entirely. */
  isLg: boolean;
  onTabChange: (tab: AgentTab) => void;
  onViewChange: (view: AgentView) => void;
}) {
  // In a side-by-side split both panes render, so neither tab is "the" pane.
  const inSplit = isLg && view === "split";
  return (
    <div data-testid="view-switcher" className="flex shrink-0 items-center gap-0.5">
      <div role="tablist" aria-label="Agent surface" className="flex items-center gap-0.5">
        <TabButton
          testid="tab-transcript"
          icon={MessagesSquare}
          label="Transcript"
          selected={!inSplit && tab === "transcript"}
          onClick={() => onTabChange("transcript")}
        />
        <TabButton
          testid="tab-terminal"
          icon={SquareTerminal}
          label="Terminal"
          live
          selected={!inSplit && tab === "terminal"}
          onClick={() => onTabChange("terminal")}
        />
      </div>
      {isLg && (
        <div className="ml-0.5 flex items-center gap-0.5">
          <ViewToggle
            testid="view-tabs"
            label="Single pane"
            active={view === "tabs"}
            onClick={() => onViewChange("tabs")}
          >
            <Square className="size-3.5" />
          </ViewToggle>
          <ViewToggle
            testid="view-split"
            label="Split view"
            active={view === "split"}
            onClick={() => onViewChange("split")}
          >
            <Columns2 className="size-3.5" />
          </ViewToggle>
        </div>
      )}
    </div>
  );
}

/**
 * A quiet tab — text at `sm`+, its glyph alone below `sm` (the same breakpoint
 * where the header brand logo drops) so the header identity zone keeps its width
 * on phones: icon-only tabs reclaim ~70px for the truncating title, which would
 * otherwise collapse to a single character. The file-tab feel without the
 * `TabsList` pill; `aria-label` names the tab in both modes.
 */
function TabButton({
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
  /** Terminal carries a pulsing live dot — the always-fresh cue, shown in both modes. */
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
        selected
          ? "bg-accent font-medium text-foreground"
          : "text-muted-foreground hover:text-foreground",
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

/** The lg-only single/split view toggle — an icon ghost, no bordered pill. */
function ViewToggle({
  active,
  onClick,
  label,
  testid,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  testid: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      data-testid={testid}
      aria-label={label}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "inline-flex size-6 items-center justify-center rounded-md transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background",
        active ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
