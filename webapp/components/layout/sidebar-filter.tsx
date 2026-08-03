"use client";

import { BellRing, EyeOff, Filter } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { AgentStateMark } from "@/components/shared/state-mark";
import { AGENT_STATE_LABEL } from "@/lib/grove/agent-state-tokens";
import { useUiStore } from "@/lib/grove/ui-store";
import type { AgentActivityState } from "@/lib/grove/types";
import { cn } from "@/lib/utils";

/**
 * The rail's compact filter. Since the rail is a flat, section-less list,
 * this ONE quiet menu is where all organizing happens: hide states, keep only
 * attention rows, hide whole projects, and reveal the unmapped/metadata-only
 * rows. It is also the reachable clear-path that makes persisting every filter
 * safe (a filter with no UI to clear it would otherwise strand a stale state).
 *
 * Each state row leads with its `AgentStateMark` glyph (the ONE
 * agent-state icon system), "Needs attention only" with a `BellRing`, project
 * rows are plain checkboxes (hidden-set: a checked project shows), and every menu
 * row drops to the rail's dense 13px tier so the menu reads as part of the same
 * surface as the rows it filters.
 *
 * The filter model is HIDE-sets everywhere (`hiddenStates`/`hiddenProjects` — a
 * checked item = shown; empty = all show, and a state/project appearing later is
 * visible by default) plus two booleans (`attentionOnly`, `showUnmapped`).
 * Quiet-chrome: no saturated hue on the trigger — an active filter shifts it from
 * muted to foreground and shows a small neutral dot, never a colored badge.
 * Seams: `sidebar-filter-trigger`, `sidebar-filter-active`, `filter-attention-only`,
 * `filter-state-<s>`, `filter-project-<repo_root>`, `filter-show-unmapped`,
 * `filter-clear`.
 */
const FILTERABLE_STATES: AgentActivityState[] = [
  "working",
  "waiting",
  "blocked",
  "starting",
  "idle",
  "error",
];

export function SidebarFilter({
  projects = [],
}: {
  /** The deduped project list (from `facets.projects`) — one checkbox row each. */
  projects?: { repo_root: string; repo_name: string }[];
}) {
  const hiddenStates = useUiStore((s) => s.hiddenStates);
  const toggleState = useUiStore((s) => s.toggleState);
  const hiddenProjects = useUiStore((s) => s.hiddenProjects);
  const toggleProject = useUiStore((s) => s.toggleProject);
  const attentionOnly = useUiStore((s) => s.attentionOnly);
  const setAttentionOnly = useUiStore((s) => s.setAttentionOnly);
  const showUnmapped = useUiStore((s) => s.showUnmapped);
  const setShowUnmapped = useUiStore((s) => s.setShowUnmapped);
  const clearFilters = useUiStore((s) => s.clearFilters);
  const active =
    attentionOnly || hiddenStates.length > 0 || hiddenProjects.length > 0 || showUnmapped;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          data-testid="sidebar-filter-trigger"
          aria-label={active ? "Filter sessions (active)" : "Filter sessions"}
          className={cn("relative shrink-0", active ? "text-foreground" : "text-muted-foreground")}
        >
          <Filter />
          {active && (
            <span
              data-testid="sidebar-filter-active"
              aria-hidden
              className="absolute right-1 top-1 size-1.5 rounded-full bg-foreground"
            />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        {/* Keep the menu open across toggles (preventDefault) so a run of state
            flips doesn't dismiss it after each click. */}
        <DropdownMenuCheckboxItem
          data-testid="filter-attention-only"
          className="gap-2 text-[13px]"
          checked={attentionOnly}
          onCheckedChange={(v) => setAttentionOnly(Boolean(v))}
          onSelect={(e) => e.preventDefault()}
        >
          <BellRing className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          Needs attention only
        </DropdownMenuCheckboxItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Show states</DropdownMenuLabel>
        {FILTERABLE_STATES.map((s) => (
          <DropdownMenuCheckboxItem
            key={s}
            data-testid={`filter-state-${s}`}
            className="gap-2 text-[13px]"
            checked={!hiddenStates.includes(s)}
            onCheckedChange={() => toggleState(s)}
            onSelect={(e) => e.preventDefault()}
          >
            <AgentStateMark state={s} className="text-[13px]" />
            {AGENT_STATE_LABEL[s]}
          </DropdownMenuCheckboxItem>
        ))}
        {projects.length > 0 && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Projects</DropdownMenuLabel>
            {projects.map((p) => (
              <DropdownMenuCheckboxItem
                key={p.repo_root}
                data-testid={`filter-project-${p.repo_root}`}
                className="gap-2 text-[13px]"
                checked={!hiddenProjects.includes(p.repo_root)}
                onCheckedChange={() => toggleProject(p.repo_root)}
                onSelect={(e) => e.preventDefault()}
              >
                <span className="truncate">{p.repo_name}</span>
              </DropdownMenuCheckboxItem>
            ))}
          </>
        )}
        <DropdownMenuSeparator />
        {/* The debugging escape hatch: metadata-only rows have no live workspace
            to open, so they're hidden by default — this reveals them. */}
        <DropdownMenuCheckboxItem
          data-testid="filter-show-unmapped"
          className="gap-2 text-[13px]"
          checked={showUnmapped}
          onCheckedChange={(v) => setShowUnmapped(Boolean(v))}
          onSelect={(e) => e.preventDefault()}
        >
          <EyeOff className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          Show unmapped sessions
        </DropdownMenuCheckboxItem>
        <p className="px-2 pb-1 pt-0.5 text-[11px] leading-snug text-muted-foreground">
          History-only rows with no live workspace to open.
        </p>
        {active && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              data-testid="filter-clear"
              className="text-[13px]"
              onSelect={(e) => {
                e.preventDefault();
                clearFilters();
              }}
            >
              Clear filters
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
