"use client";

import { CheckIcon, ListFilterIcon } from "lucide-react";

import { AgentMark } from "@/components/grove/agent-mark";
import { BranchLabel, LocationLabel, ProjectLabel } from "@/components/grove/entity";
import type { Facet } from "@/components/grove/facets";
import { toggleHidden } from "@/components/grove/facets";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import {
  activeSessionFilterCount,
  isBareLocation,
  NO_SESSION_FILTER,
  parseBound,
  type SessionFacets,
  type SessionFilter,
} from "./filter";

/**
 * The catalog's one organizing instrument, beside the search box.
 *
 * Everything that narrows the list lives behind this single button, which also
 * makes it the reachable clear-path: no combination of filters can strand the
 * table empty with no visible way back.
 *
 * Every group hides its own header when it would offer fewer than two choices.
 * A host running only Claude Code has nothing to decide about the agent
 * dimension, and a menu that lists one option with the full row count beside it
 * is chrome pretending to be a control.
 *
 * WHY `aria-pressed` RATHER THAN `menuitemcheckbox`, which is what the fleet's
 * equivalent menu uses: `menuitemcheckbox` is only valid inside a `role="menu"`
 * container, and a menu may not contain a textbox — which this one does, for
 * the turn range. Toggle buttons are valid in any container and say the same
 * thing to a screen reader, so the mixed content is what decides it.
 */
export function SessionFilterMenu({
  filter,
  onFilterChange,
  facets,
  maxTurns,
}: {
  filter: SessionFilter;
  onFilterChange: (filter: SessionFilter) => void;
  facets: SessionFacets;
  /** The widest count in the catalog. Advertised as each box's `max` — which
   * bounds the spinner and tells a reader what the scale is — never used to
   * clamp what they type. See `parseBound`. */
  maxTurns: number;
}): React.ReactNode {
  const active = activeSessionFilterCount(filter);
  const rangeSet = filter.minTurns !== null || filter.maxTurns !== null;

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className="h-9 shrink-0 gap-1.5 px-2.5"
          aria-label={active > 0 ? `Filter sessions (${active} active)` : "Filter sessions"}
          data-testid="session-filter-trigger"
          data-active={active > 0}
        >
          <ListFilterIcon className="size-4" aria-hidden />
          Filter
          {active > 0 ? (
            <Badge variant="secondary" className="px-1 text-xs tabular-nums">
              {active}
            </Badge>
          ) : null}
        </Button>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        className="max-h-[28rem] w-72 overflow-y-auto p-1"
        data-testid="session-filter-menu"
      >
        <FilterGroup label="Turns">
          {facets.counted === 0 ? (
            /* A range over a dimension nothing reports is a trap, not a
               control: the first keystroke would empty the whole table. The
               host-wide scan leaves every count null today, so say that
               instead of offering the boxes. */
            <p className="text-muted-foreground px-2 pb-2 text-xs" data-testid="session-filter-nocounts">
              No turn counts at this scope yet.
            </p>
          ) : (
            <>
              <div className="flex items-center gap-2 px-2 pb-2">
                <TurnBound
                  label="Minimum turns"
                  placeholder="min"
                  value={filter.minTurns}
                  ceiling={maxTurns}
                  onChange={(minTurns) => onFilterChange({ ...filter, minTurns })}
                />
                <span aria-hidden className="text-muted-foreground text-xs">
                  to
                </span>
                <TurnBound
                  label="Maximum turns"
                  placeholder="max"
                  value={filter.maxTurns}
                  ceiling={maxTurns}
                  onChange={(maxTurns) => onFilterChange({ ...filter, maxTurns })}
                />
              </div>
              {/* The cost of the bound, stated where the bound is set. A count
                  cannot be satisfied by a session that has none, so an active
                  range hides the uncounted ones — silently, unless this says
                  so. */}
              {rangeSet && facets.uncounted > 0 ? (
                <p
                  className="text-muted-foreground px-2 pb-2 text-xs"
                  data-testid="session-filter-uncounted"
                >
                  Hiding {facets.uncounted} session{facets.uncounted === 1 ? "" : "s"} with no
                  counted turns.
                </p>
              ) : null}
            </>
          )}
        </FilterGroup>

        <FacetGroup
          label="Location"
          facets={facets.locations}
          hidden={filter.hiddenLocations}
          testId="location"
          onToggle={(id) =>
            onFilterChange({ ...filter, hiddenLocations: toggleHidden(filter.hiddenLocations, id) })
          }
          render={(facet) =>
            isBareLocation(facet) ? (
              <LocationLabel path={facet.label} />
            ) : (
              <ProjectLabel name={facet.label} />
            )
          }
        />

        <FacetGroup
          label="Agent"
          facets={facets.agents}
          hidden={filter.hiddenAgents}
          testId="agent"
          onToggle={(id) =>
            onFilterChange({ ...filter, hiddenAgents: toggleHidden(filter.hiddenAgents, id) })
          }
          render={(facet) => (
            <span className="flex min-w-0 items-center gap-1.5">
              <AgentMark agentName={facet.id} className="size-3.5 shrink-0" />
              <span className="truncate">{facet.label}</span>
            </span>
          )}
        />

        <FacetGroup
          label="Branch"
          facets={facets.branches}
          hidden={filter.hiddenBranches}
          testId="branch"
          onToggle={(id) =>
            onFilterChange({ ...filter, hiddenBranches: toggleHidden(filter.hiddenBranches, id) })
          }
          render={(facet) => <BranchLabel name={facet.label} />}
        />

        {active > 0 ? (
          <>
            <Separator className="my-1" />
            <Button
              variant="ghost"
              size="sm"
              className="h-8 w-full justify-start px-2 text-sm font-normal"
              onClick={() => onFilterChange({ ...NO_SESSION_FILTER, query: filter.query })}
              data-testid="session-filter-clear"
            >
              Clear filters
            </Button>
          </>
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

/**
 * One end of the range. Empty means UNBOUNDED, which is why the value is
 * `number | null` rather than a number with a sentinel: clearing the box has to
 * restore the whole list, and `0` is a legitimate bound a user may want.
 */
function TurnBound({
  label,
  placeholder,
  value,
  ceiling,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: number | null;
  ceiling: number;
  onChange: (value: number | null) => void;
}): React.ReactNode {
  return (
    <Input
      type="number"
      inputMode="numeric"
      min={0}
      max={ceiling || undefined}
      className="h-8 min-w-0 flex-1 text-sm tabular-nums"
      aria-label={label}
      placeholder={placeholder}
      value={value ?? ""}
      onChange={(event) => onChange(parseBound(event.target.value))}
      data-testid={`session-filter-${placeholder}-turns`}
    />
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactNode {
  return (
    <div role="group" aria-label={label}>
      <p className="text-muted-foreground px-2 pt-2 pb-1 text-xs font-medium">{label}</p>
      {children}
    </div>
  );
}

/** A dimension's options, or nothing at all when there is no choice to make. */
function FacetGroup({
  label,
  facets,
  hidden,
  testId,
  onToggle,
  render,
}: {
  label: string;
  facets: readonly Facet[];
  hidden: readonly string[];
  testId: string;
  onToggle: (id: string) => void;
  render: (facet: Facet) => React.ReactNode;
}): React.ReactNode {
  if (facets.length < 2) return null;
  return (
    <>
      <Separator className="my-1" />
      <FilterGroup label={label}>
        {facets.map((facet) => (
          <FilterRow
            key={facet.id}
            checked={!hidden.includes(facet.id)}
            onToggle={() => onToggle(facet.id)}
            count={facet.count}
            testId={`session-filter-${testId}-${facet.id}`}
          >
            {render(facet)}
          </FilterRow>
        ))}
      </FilterGroup>
    </>
  );
}

/**
 * One checkable row. A `Button` with `aria-pressed` rather than a checkbox
 * input because `components/ui/` ships no checkbox primitive, and inventing one
 * here would be the bespoke component this app exists to avoid — the vendored
 * `Button` already carries the hover, focus ring and radius the row needs.
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
      aria-pressed={checked}
      onClick={onToggle}
      className="h-8 w-full justify-start gap-2 px-2 text-sm font-normal"
      data-testid={testId}
    >
      <CheckIcon
        aria-hidden
        className={checked ? "size-4 shrink-0" : "size-4 shrink-0 opacity-0"}
      />
      <span className="min-w-0 flex-1 truncate text-left">{children}</span>
      <span className="text-muted-foreground text-xs tabular-nums">{count}</span>
    </Button>
  );
}
