"use client";

import { BellRingIcon, CheckIcon, ListFilterIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { activeFilterCount, NO_FILTER, type FleetFacets, type FleetFilter } from "./filter";
import { agentLabel } from "./tokens";
import type { AgentState } from "./types";

/**
 * The rail's one organizing instrument.
 *
 * Grouping was the thing this replaces: a Grove workspace is often empty and
 * often momentary, so sections keyed on the repo left the list mostly headings.
 * A flat list answers "what moved last" on its own, and everything else a
 * person might want to narrow by lives behind this one button — which also
 * makes it the reachable clear-path, so no filter can strand the list empty
 * with no visible way back.
 */
export function FleetFilterMenu({
  filter,
  onFilterChange,
  facets,
}: {
  filter: FleetFilter;
  onFilterChange: (filter: FleetFilter) => void;
  facets: FleetFacets;
}): React.ReactNode {
  const active = activeFilterCount(filter);

  const toggleState = (state: AgentState): void =>
    onFilterChange({
      ...filter,
      hiddenStates: filter.hiddenStates.includes(state)
        ? filter.hiddenStates.filter((held) => held !== state)
        : [...filter.hiddenStates, state],
    });

  const toggleProject = (repoRoot: string): void =>
    onFilterChange({
      ...filter,
      hiddenProjects: filter.hiddenProjects.includes(repoRoot)
        ? filter.hiddenProjects.filter((held) => held !== repoRoot)
        : [...filter.hiddenProjects, repoRoot],
    });

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 shrink-0 gap-1 px-2"
          aria-label={active > 0 ? `Filter workspaces (${active} active)` : "Filter workspaces"}
          data-testid="fleet-filter-trigger"
          data-active={active > 0}
        >
          <ListFilterIcon className="size-4" />
          {active > 0 ? (
            <Badge variant="secondary" className="px-1 text-xs tabular-nums">
              {active}
            </Badge>
          ) : null}
        </Button>
      </PopoverTrigger>

      {/* `role="menu"` because the rows below are `menuitemcheckbox`, and that
          role is only valid inside a menu container. */}
      <PopoverContent align="start" role="menu" className="w-64 p-1" data-testid="fleet-filter-menu">
        <FilterRow
          checked={filter.attentionOnly}
          onToggle={() => onFilterChange({ ...filter, attentionOnly: !filter.attentionOnly })}
          count={facets.attention}
          testId="fleet-filter-attention"
        >
          <BellRingIcon className="text-muted-foreground size-4 shrink-0" aria-hidden />
          Needs attention
        </FilterRow>

        {facets.states.length > 0 ? (
          <>
            <Separator className="my-1" />
            <FilterLabel>Agent state</FilterLabel>
            {facets.states.map(({ state, count }) => (
              <FilterRow
                key={state}
                checked={!filter.hiddenStates.includes(state)}
                onToggle={() => toggleState(state)}
                count={count}
                testId={`fleet-filter-state-${state}`}
              >
                <span className="truncate">{agentLabel(state)}</span>
              </FilterRow>
            ))}
          </>
        ) : null}

        {facets.projects.length > 1 ? (
          <>
            <Separator className="my-1" />
            <FilterLabel>Project</FilterLabel>
            {facets.projects.map(({ repoRoot, repoName, count }) => (
              <FilterRow
                key={repoRoot}
                checked={!filter.hiddenProjects.includes(repoRoot)}
                onToggle={() => toggleProject(repoRoot)}
                count={count}
                testId={`fleet-filter-project-${repoRoot}`}
              >
                <span className="truncate">{repoName}</span>
              </FilterRow>
            ))}
          </>
        ) : null}

        {active > 0 ? (
          <>
            <Separator className="my-1" />
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-full justify-start px-2 text-sm font-normal"
              onClick={() => onFilterChange({ ...NO_FILTER, query: filter.query })}
              data-testid="fleet-filter-clear"
            >
              Clear filters
            </Button>
          </>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function FilterLabel({ children }: { children: React.ReactNode }): React.ReactNode {
  return <p className="text-muted-foreground px-2 pt-2 pb-1 text-xs font-medium">{children}</p>;
}

/**
 * One checkable row. `aria-checked` on a `menuitemcheckbox` rather than a
 * checkbox input because `components/ui/` ships no checkbox primitive, and
 * inventing one here would be exactly the bespoke component this app exists to
 * avoid — the vendored `Button` already carries the hover, focus ring and
 * radius this row needs.
 */
function FilterRow({
  checked,
  onToggle,
  count,
  testId,
  children,
}: {
  checked: boolean;
  onToggle: () => void;
  count: number;
  testId: string;
  children: React.ReactNode;
}): React.ReactNode {
  return (
    <Button
      variant="ghost"
      size="sm"
      role="menuitemcheckbox"
      aria-checked={checked}
      onClick={onToggle}
      className="h-8 w-full justify-start gap-2 px-2 text-sm font-normal"
      data-testid={testId}
    >
      <CheckIcon className={checked ? "size-4 shrink-0" : "size-4 shrink-0 opacity-0"} aria-hidden />
      {children}
      <span className="text-muted-foreground ml-auto text-xs tabular-nums">{count}</span>
    </Button>
  );
}
