"use client";

import { BellRingIcon, FolderTreeIcon, ListFilterIcon } from "lucide-react";

import type { LucideIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  activeFilterCount,
  NO_FILTER,
  type FleetFacets,
  type FleetFilter,
} from "./filter";
import { agentGlyph, agentLabel } from "./tokens";
import type { AgentState } from "./types";

/** Project hide sets belong to the dashboard, not beside a project scope control. */
export function showsProjectHideControls(
  variant: "sidebar" | "fleet",
  facets: FleetFacets,
): boolean {
  return variant === "fleet" && facets.projects.length > 1;
}

/** The fleet's one organizing instrument and its reachable clear path. */
export function FleetFilterMenu({
  filter,
  onFilterChange,
  facets,
  variant = "fleet",
}: {
  filter: FleetFilter;
  onFilterChange: (filter: FleetFilter) => void;
  facets: FleetFacets;
  /** The rail has one real compact consumer; the dashboard keeps its labelled toolbar control. */
  variant?: "sidebar" | "fleet";
}): React.ReactNode {
  const active = activeFilterCount(filter);
  const compact = variant === "sidebar";

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
    <DropdownMenu>
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
        <Button
          variant={compact ? "outline" : "ghost"}
          size="sm"
          className={compact ? "bg-transparent dark:bg-transparent border-edge-control size-6 min-h-[24px] min-w-[24px] px-1.5 [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11" : "h-7 shrink-0 gap-1.5 px-2"}
          aria-label={
            filter.groupBy === "project"
              ? "Filter workspaces, grouped by project"
              : active > 0
                ? `Filter workspaces (${active} active)`
                : "Filter workspaces"
          }
          data-testid="fleet-filter-trigger"
          data-active={active > 0}
        >
          <ListFilterIcon className="size-3.5" aria-hidden />
          {compact ? null : <span className="text-xs">Filters</span>}
          {active > 0 ? (
            <Badge variant="secondary" className="px-1 text-xs tabular-nums">
              {active}
            </Badge>
          ) : null}
        </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent>{active > 0 ? `Filter workspaces (${active} active)` : "Filter workspaces"}</TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DropdownMenuContent
        align="start"
        className="max-h-[28rem] w-64 overflow-y-auto"
        data-testid="fleet-filter-menu"
      >
        <DropdownMenuLabel>Arrange</DropdownMenuLabel>
        <FilterCheckbox
          checked={filter.groupBy === "project"}
          onCheckedChange={() =>
            onFilterChange({
              ...filter,
              groupBy: filter.groupBy === "project" ? "none" : "project",
            })
          }
          testId="fleet-filter-group-project"
          icon={FolderTreeIcon}
        >
          Group by project
        </FilterCheckbox>

        <DropdownMenuSeparator />
        <DropdownMenuLabel>Filter</DropdownMenuLabel>
        <FilterCheckbox
          checked={filter.attentionOnly}
          onCheckedChange={() =>
            onFilterChange({ ...filter, attentionOnly: !filter.attentionOnly })
          }
          count={facets.attention}
          testId="fleet-filter-attention"
          icon={BellRingIcon}
        >
          Needs attention
        </FilterCheckbox>

        {facets.states.length > 0 ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Agent state</DropdownMenuLabel>
            {facets.states.map(({ state, count }) => (
              <FilterCheckbox
                key={state}
                checked={!filter.hiddenStates.includes(state)}
                onCheckedChange={() => toggleState(state)}
                count={count}
                testId={`fleet-filter-state-${state}`}
                icon={agentGlyph(state)}
              >
                <span className="truncate">{agentLabel(state)}</span>
              </FilterCheckbox>
            ))}
          </>
        ) : null}

        {showsProjectHideControls(variant, facets) ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Project</DropdownMenuLabel>
            {facets.projects.map(({ repoRoot, repoName, count }) => (
              <FilterCheckbox
                key={repoRoot}
                checked={!filter.hiddenProjects.includes(repoRoot)}
                onCheckedChange={() => toggleProject(repoRoot)}
                count={count}
                testId={`fleet-filter-project-${repoRoot}`}
              >
                <span className="truncate">{repoName}</span>
              </FilterCheckbox>
            ))}
          </>
        ) : null}

        {active > 0 ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={() =>
                onFilterChange({
                  ...NO_FILTER,
                  query: filter.query,
                  groupBy: filter.groupBy,
                })
              }
              data-testid="fleet-filter-clear"
            >
              Clear filters
            </DropdownMenuItem>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** A persistent native menu checkbox with the fleet's count and mark slots. */
function FilterCheckbox({
  checked,
  onCheckedChange,
  count,
  testId,
  icon: Icon,
  children,
}: {
  checked: boolean;
  onCheckedChange: () => void;
  count?: number;
  testId: string;
  icon?: LucideIcon;
  children: React.ReactNode;
}): React.ReactNode {
  return (
    <DropdownMenuCheckboxItem
      checked={checked}
      onCheckedChange={onCheckedChange}
      onSelect={(event) => event.preventDefault()}
      data-testid={testId}
    >
      {Icon ? (
        <Icon className="size-3.5 shrink-0 text-content-tertiary" aria-hidden />
      ) : null}
      {children}
      {count === undefined ? null : (
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {count}
        </span>
      )}
    </DropdownMenuCheckboxItem>
  );
}
