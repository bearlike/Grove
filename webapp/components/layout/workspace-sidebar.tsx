"use client";

import { useMemo } from "react";
import { Bell, FolderGit2, Layers, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { AgentStateMark } from "@/components/shared/state-mark";
import { useActivityStream, useDaemonWhoami } from "@/lib/grove/hooks";
import { computeFacets, type DashboardFacets } from "@/lib/grove/dashboard-filter";
import { agentStateLabel } from "@/lib/grove/agent-state-tokens";
import { useUiStore } from "@/lib/grove/ui-store";
import { cn } from "@/lib/utils";
import type { AgentActivityState } from "@/lib/grove/types";

/**
 * The persistent left rail — a Devin-style navigation/scope surface (issue #96
 * deliverable B). It carries ONLY *view intent*: a search box, a repo SCOPE
 * switcher, agent-state filter chips, and the daemon-identity footer. It no
 * longer duplicates the workspace card grid; navigation to a workspace detail
 * page happens by clicking a card, not a rail row. Every control writes the one
 * Zustand UI store (`query`, `scopeRepo`, `hiddenStates`, `attentionOnly`); the
 * grid reads the same store and re-presents itself.
 *
 * Live counts (per-repo + per-state + attention) come from `computeFacets` over
 * the authoritative `useActivityStream().snapshot.projects` — the same snapshot
 * the engine emits a group-per-`known_roots()` for, so empty / config-declared
 * repos (#95) still get a scope row and you can scope into a repo with zero
 * workspaces.
 *
 * Color discipline: terracotta `--primary` never appears here. Status hue shows
 * only as small `AgentStateMark` glyphs; the active scope/chip is carried by a
 * neutral `bg-accent` fill, structure not saturation.
 *
 * Positioning is the consumer's job (mechanism, not policy): the (shell) layout
 * passes the sticky desktop-rail classes via `className`, and renders a second
 * instance inside a mobile `Sheet` with `onClose`/`onNavigate` to dismiss the
 * drawer. Test seams: `workspace-sidebar`, `sidebar-search`, `sidebar-repo-all`,
 * `sidebar-repo` (+ `data-repo`), `sidebar-footer`.
 */
export function WorkspaceSidebar({
  className,
  onClose,
  onNavigate,
}: {
  className?: string;
  /** Mobile (Sheet) only — renders the close control. Desktop rail omits it. */
  onClose?: () => void;
  /** Called after a scope/filter click so the mobile drawer can dismiss itself. */
  onNavigate?: () => void;
}) {
  const { snapshot } = useActivityStream();
  const facets = useMemo<DashboardFacets | null>(
    () => (snapshot ? computeFacets(snapshot) : null),
    [snapshot],
  );

  return (
    <aside
      data-testid="workspace-sidebar"
      className={cn("flex min-h-0 flex-col bg-sidebar text-sidebar-foreground", className)}
    >
      <div className="flex items-center gap-2 px-3 pt-3">
        <SidebarSearch />
        {onClose && (
          <Button
            variant="ghost"
            size="icon-sm"
            className="shrink-0 lg:hidden"
            aria-label="Close sidebar"
            data-testid="sidebar-close"
            onClick={onClose}
          >
            <X />
          </Button>
        )}
      </div>

      <Separator className="mt-3 bg-sidebar-border" />

      {/* `flex-1` claims the height between the search and footer; the ScrollArea
          owns the overflow so a long repo list scrolls inside the rail. */}
      <ScrollArea className="min-h-0 flex-1">
        <div className="flex flex-col gap-5 px-2 py-3">
          <ScopeList facets={facets} onNavigate={onNavigate} />
          <StateFilters facets={facets} onNavigate={onNavigate} />
        </div>
      </ScrollArea>

      <SidebarFooter />
    </aside>
  );
}

/** Free-text search → the store's `query` (the grid filters title/branch by it). */
function SidebarSearch(): React.ReactElement {
  const query = useUiStore((s) => s.query);
  const setQuery = useUiStore((s) => s.setQuery);
  return (
    <div className="relative min-w-0 flex-1">
      <Search
        aria-hidden
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search workspaces"
        aria-label="Search workspaces"
        data-testid="sidebar-search"
        className="h-8 border-sidebar-border bg-card pl-8 text-[13px] shadow-inner placeholder:text-muted-foreground/70"
      />
    </div>
  );
}

// ─── Repo scope switcher ─────────────────────────────────────────────────────

const SECTION_LABEL =
  "px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground";

function ScopeList({
  facets,
  onNavigate,
}: {
  facets: DashboardFacets | null;
  onNavigate?: () => void;
}): React.ReactElement {
  const scopeRepo = useUiStore((s) => s.scopeRepo);
  const setScopeRepo = useUiStore((s) => s.setScopeRepo);

  const select = (repo: string | null): void => {
    setScopeRepo(repo);
    onNavigate?.();
  };

  return (
    <section aria-label="Repository scope">
      <h2 className={SECTION_LABEL}>Repositories</h2>
      {facets === null ? (
        <ScopeSkeleton />
      ) : (
        <ul className="flex flex-col gap-0.5">
          <ScopeRow
            label="All workspaces"
            icon={<Layers aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />}
            count={facets.total}
            active={scopeRepo === null}
            onSelect={() => select(null)}
            testid="sidebar-repo-all"
          />
          {facets.projects.map((p) => (
            <ScopeRow
              key={p.repo_root}
              label={p.repo_name}
              icon={
                <FolderGit2 aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
              }
              count={p.count}
              active={scopeRepo === p.repo_root}
              onSelect={() => select(p.repo_root)}
              testid="sidebar-repo"
              dataRepo={p.repo_root}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * One scope row. Active = a neutral `bg-accent` fill (structure, not status hue);
 * inactive is transparent and brightens on hover. The whole row is a focusable
 * button; the count is `tabular-nums` so the right edge never jitters.
 */
function ScopeRow({
  label,
  icon,
  count,
  active,
  onSelect,
  testid,
  dataRepo,
}: {
  label: string;
  icon: React.ReactNode;
  count: number;
  active: boolean;
  onSelect: () => void;
  testid: string;
  dataRepo?: string;
}): React.ReactElement {
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        data-testid={testid}
        data-repo={dataRepo}
        aria-pressed={active}
        title={label}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          active
            ? "bg-accent text-foreground"
            : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
        )}
      >
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <span className="shrink-0 tabular-nums text-xs text-muted-foreground/70">{count}</span>
      </button>
    </li>
  );
}

function ScopeSkeleton(): React.ReactElement {
  return (
    <div className="flex flex-col gap-1 px-2" data-testid="sidebar-scope-skeleton">
      <Skeleton className="h-7 w-full" />
      <Skeleton className="h-7 w-5/6" />
      <Skeleton className="h-7 w-2/3" />
    </div>
  );
}

// ─── Agent-state filter chips + attention toggle ─────────────────────────────

function StateFilters({
  facets,
  onNavigate,
}: {
  facets: DashboardFacets | null;
  onNavigate?: () => void;
}): React.ReactElement | null {
  const hiddenStates = useUiStore((s) => s.hiddenStates);
  const toggleState = useUiStore((s) => s.toggleState);
  const attentionOnly = useUiStore((s) => s.attentionOnly);
  const setAttentionOnly = useUiStore((s) => s.setAttentionOnly);

  // Nothing to filter until the snapshot resolves with at least one state.
  if (facets === null || facets.states.length === 0) return null;

  return (
    <section aria-label="Agent-state filters">
      <h2 className={SECTION_LABEL}>Agent state</h2>
      <div className="flex flex-wrap gap-1.5 px-2">
        <AttentionChip
          count={facets.attention}
          active={attentionOnly}
          onToggle={() => {
            setAttentionOnly(!attentionOnly);
            onNavigate?.();
          }}
        />
        {facets.states.map(({ state, count }) => (
          <StateChip
            key={state}
            state={state}
            count={count}
            // The store records states to HIDE; a chip is "on" (showing) when the
            // state is NOT in `hiddenStates`.
            active={!hiddenStates.includes(state)}
            onToggle={() => {
              toggleState(state);
              onNavigate?.();
            }}
          />
        ))}
      </div>
    </section>
  );
}

/**
 * Shared chip shell for the state + attention toggles. A pressed (active) chip
 * gets the neutral `bg-accent` fill; an inactive one is a quiet outline that
 * brightens on hover. Built on the `xs` Button so it inherits the focus-ring and
 * dense geometry — never a hand-rolled pill.
 */
function FilterChip({
  active,
  onToggle,
  label,
  testid,
  dataState,
  children,
}: {
  active: boolean;
  onToggle: () => void;
  label: string;
  testid: string;
  dataState?: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <Button
      type="button"
      size="xs"
      variant={active ? "secondary" : "ghost"}
      onClick={onToggle}
      aria-pressed={active}
      aria-label={label}
      title={label}
      data-testid={testid}
      data-state-key={dataState}
      className={cn(
        "rounded-full border",
        active
          ? "border-transparent bg-accent text-foreground"
          : "border-border text-muted-foreground hover:bg-muted/60 hover:text-foreground",
      )}
    >
      {children}
    </Button>
  );
}

function StateChip({
  state,
  count,
  active,
  onToggle,
}: {
  state: AgentActivityState;
  count: number;
  active: boolean;
  onToggle: () => void;
}): React.ReactElement {
  const label = `${agentStateLabel(state)} (${count})`;
  return (
    <FilterChip
      active={active}
      onToggle={onToggle}
      label={label}
      testid="sidebar-state-chip"
      dataState={state}
    >
      <AgentStateMark state={state} className="text-xs" />
      <span className="capitalize">{agentStateLabel(state)}</span>
      <span className="tabular-nums text-muted-foreground/70">{count}</span>
    </FilterChip>
  );
}

function AttentionChip({
  count,
  active,
  onToggle,
}: {
  count: number;
  active: boolean;
  onToggle: () => void;
}): React.ReactElement {
  return (
    <FilterChip
      active={active}
      onToggle={onToggle}
      label={`Attention only (${count})`}
      testid="sidebar-attention-toggle"
    >
      <Bell
        aria-hidden
        className="size-3.5"
        style={{ color: "var(--ref-info)" }}
      />
      <span>Attention</span>
      <span className="tabular-nums text-muted-foreground/70">{count}</span>
    </FilterChip>
  );
}

// ─── Footer: daemon identity (version lives in the status bar, not here) ──────

function SidebarFooter(): React.ReactElement | null {
  const { data: whoami } = useDaemonWhoami();
  if (!whoami) return null;
  return (
    <>
      <Separator className="bg-sidebar-border" />
      <div
        data-testid="sidebar-footer"
        className="flex items-center gap-2 px-3 py-2.5 text-xs text-muted-foreground"
      >
        <span
          aria-hidden
          className="grid size-6 shrink-0 place-items-center rounded-md bg-accent text-[11px] font-semibold uppercase leading-none text-foreground"
        >
          {whoami.user.slice(0, 1)}
        </span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px]">
          {whoami.user}@{whoami.host}
        </span>
      </div>
    </>
  );
}
