"use client";

import { Check, ListFilter } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  agentStateColor,
  agentStateGlyph,
  agentStateLabel,
} from "@/lib/grove/agent-state-tokens";
import {
  activeFilterCount,
  type DashboardFacets,
  type DashboardFilterState,
} from "@/lib/grove/dashboard-filter";
import type { AgentActivityState } from "@/lib/grove/types";
import { cn } from "@/lib/utils";

/**
 * One consolidated filter for the whole wall: which projects, which agent
 * states, and an attention-only switch — replacing the three lens tabs. Stored
 * as "hidden" sets (see DashboardFilterState) so everything is on by default and
 * states/projects that appear later stay visible. Every option shows a live
 * count, so "nothing under Active" is self-explanatory (you can see `working: 0`).
 */
export function DashboardFilter({
  facets,
  value,
  onChange,
}: {
  facets: DashboardFacets;
  value: DashboardFilterState;
  onChange: (next: DashboardFilterState) => void;
}) {
  const count = activeFilterCount(value);

  const toggleProject = (repoRoot: string) => {
    const next = new Set(value.hiddenProjects);
    next.has(repoRoot) ? next.delete(repoRoot) : next.add(repoRoot);
    onChange({ ...value, hiddenProjects: next });
  };
  const toggleState = (state: AgentActivityState) => {
    const next = new Set(value.hiddenStates);
    next.has(state) ? next.delete(state) : next.add(state);
    onChange({ ...value, hiddenStates: next });
  };
  const showAllProjects = () => onChange({ ...value, hiddenProjects: new Set() });
  const hideAllProjects = () =>
    onChange({ ...value, hiddenProjects: new Set(facets.projects.map((p) => p.repo_root)) });
  const showAllStates = () => onChange({ ...value, hiddenStates: new Set() });
  const hideAllStates = () =>
    onChange({ ...value, hiddenStates: new Set(facets.states.map((s) => s.state)) });
  const reset = () => onChange({ hiddenProjects: new Set(), hiddenStates: new Set(), attentionOnly: false });

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline" size="sm" data-testid="dashboard-filter">
          <ListFilter />
          <span>Filter</span>
          {count > 0 && (
            <Badge
              variant="secondary"
              className="ml-0.5 h-5 min-w-5 justify-center px-1 tabular-nums"
              data-testid="filter-count"
            >
              {count}
            </Badge>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-72 p-0">
        <div className="max-h-[70dvh] overflow-auto">
          <FilterSection
            title="Projects"
            onAll={showAllProjects}
            onNone={hideAllProjects}
          >
            {facets.projects.length === 0 && <Empty />}
            {facets.projects.map((p) => (
              <FilterRow
                key={p.repo_root}
                testId={`filter-project-${p.repo_root}`}
                checked={!value.hiddenProjects.has(p.repo_root)}
                label={p.repo_name}
                count={p.count}
                onClick={() => toggleProject(p.repo_root)}
              />
            ))}
          </FilterSection>

          <Separator />

          <FilterSection title="Agent state" onAll={showAllStates} onNone={hideAllStates}>
            {facets.states.length === 0 && <Empty />}
            {facets.states.map((s) => (
              <FilterRow
                key={s.state}
                testId={`filter-state-${s.state}`}
                checked={!value.hiddenStates.has(s.state)}
                label={agentStateLabel(s.state)}
                count={s.count}
                glyph={agentStateGlyph(s.state)}
                glyphColor={`var(--agent-${s.state})`}
                onClick={() => toggleState(s.state)}
              />
            ))}
          </FilterSection>

          <Separator />

          <div className="p-1">
            <FilterRow
              testId="filter-attention"
              checked={value.attentionOnly}
              label="Needs attention only"
              count={facets.attention}
              onClick={() => onChange({ ...value, attentionOnly: !value.attentionOnly })}
            />
          </div>

          <Separator />

          <div className="flex items-center justify-between p-2">
            <span className="text-xs text-muted-foreground">
              {count === 0 ? "Showing everything" : `${count} filter${count === 1 ? "" : "s"} active`}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-7"
              onClick={reset}
              disabled={count === 0}
              data-testid="filter-reset"
            >
              Reset
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

function FilterSection({
  title,
  onAll,
  onNone,
  children,
}: {
  title: string;
  onAll: () => void;
  onNone: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="p-1">
      <div className="flex items-center justify-between px-2 py-1.5">
        <span className="text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
          {title}
        </span>
        <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
          <button type="button" onClick={onAll} className="rounded px-1 hover:text-foreground hover:underline">
            All
          </button>
          <span aria-hidden>·</span>
          <button type="button" onClick={onNone} className="rounded px-1 hover:text-foreground hover:underline">
            None
          </button>
        </span>
      </div>
      {children}
    </div>
  );
}

function FilterRow({
  testId,
  checked,
  label,
  count,
  glyph,
  glyphColor,
  onClick,
}: {
  testId: string;
  checked: boolean;
  label: string;
  count: number;
  glyph?: string;
  glyphColor?: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemcheckbox"
      aria-checked={checked}
      data-testid={testId}
      data-checked={checked}
      onClick={onClick}
      className="flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-sm transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
    >
      <span
        className={cn(
          "grid size-4 shrink-0 place-items-center rounded-[4px] border",
          checked ? "border-primary bg-primary text-primary-foreground" : "border-border",
        )}
        aria-hidden
      >
        {checked && <Check className="size-3" strokeWidth={3} />}
      </span>
      {glyph && (
        <span aria-hidden className="font-mono leading-none" style={{ color: glyphColor }}>
          {glyph}
        </span>
      )}
      <span className={cn("truncate", !checked && "text-muted-foreground")}>{label}</span>
      <span className="ml-auto shrink-0 tabular-nums text-xs text-muted-foreground">{count}</span>
    </button>
  );
}

function Empty() {
  return <div className="px-2 py-1.5 text-xs italic text-muted-foreground">none</div>;
}
