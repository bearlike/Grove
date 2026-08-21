"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { MoreHorizontalIcon, PlusIcon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ThreadListSearch } from "@/components/assistant-ui/thread-list";
import { ConnectionState } from "@/components/elements/connection-state";
import { ErrorState } from "@/components/elements/error-state";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { AgentMark } from "@/components/grove/agent-mark";
import {
  BranchLabel,
  PROJECT_MIN_WIDTH,
  ProjectLabel,
} from "@/components/grove/entity";
import { absoluteTime, RelativeTime } from "@/components/grove/relative-time";
import { FleetFilterMenu } from "./fleet-filter";
import {
  RenameWorkspaceDialog,
  type WorkspaceEditField,
} from "./rename-workspace-dialog";
import {
  activeFilterCount,
  agentStateOf,
  filterRows,
  fleetFacets,
  lastActivityIso,
  NO_FILTER,
  sortedFleetRows,
  type FleetFilter,
} from "./filter";
import { agentAccent, agentGlyph, agentLabel, agentTone, phaseGlyph, phaseLabel } from "./tokens";
import type { AgentState, FleetRow } from "./types";
import type { FleetStream } from "./use-fleet";

/**
 * The trailing mark's colour, read off the SAME two tables `AgentStateBadge`
 * reads — never a second state → hue mapping. `agentAccent` is non-`undefined`
 * exactly for the mid-flight states (`starting`/`working`); `agentTone` is
 * `"destructive"` exactly for the ones that need a human (`waiting`/`blocked`/
 * `error`). A 12px inline glyph cannot carry a filled badge, so this asks for
 * the bare `text-*` sibling of the same token family instead of composing one.
 * Everything else (idle, unknown) stays neutral, per §4.1's "neutral is the
 * default" — the glyph shape is still the first carrier; this is only the
 * second, per §4.7.
 */
export function activityHue(state: AgentState): string | undefined {
  if (agentAccent(state) !== undefined) return "text-warning";
  return agentTone(state) === "destructive" ? "text-destructive" : undefined;
}

/**
 * The fleet as a flat, reverse-chronological list, in the slot assistant-ui's
 * rail gives `ThreadList`.
 *
 * The anatomy is theirs: the primary "new" action is the FIRST child, search
 * sits directly beneath it, and the rows follow. What changed from the earlier
 * version is that the rows are no longer sectioned by repo — grouping a fleet
 * by project reads as mostly headings, because a workspace is frequently empty
 * and frequently momentary. Recency answers "which one did I mean" better than
 * provenance does, and provenance moved into the filter menu.
 *
 * Collapsed content is hidden with `inert` + opacity rather than unmounted, so
 * the rail's width transition has something to animate against and the list
 * does not re-mount (and re-scroll) on every toggle.
 */
export function FleetTree({
  collapsed,
  stream,
}: {
  collapsed: boolean;
  stream: FleetStream;
}): React.ReactNode {
  const pathname = usePathname();
  const [filter, setFilter] = useState<FleetFilter>(NO_FILTER);

  const rows = useMemo(
    () => sortedFleetRows(stream.snapshot),
    [stream.snapshot],
  );
  const facets = useMemo(() => fleetFacets(rows), [rows]);
  const visible = useMemo(() => filterRows(rows, filter), [rows, filter]);

  const loading = stream.isPending && stream.snapshot === undefined;
  // Same predicate the dashboard uses, so "you have filtered something out"
  // means one thing in both places rather than being re-derived per surface.
  const filtering = filter.query.trim() !== "" || activeFilterCount(filter) > 0;

  /**
   * Whether to offer the instruments that NARROW the list — the search box and
   * the filter menu. The upstream anatomy this was ported from gates its search
   * on `hasThreads` for a reason: a control for narrowing a list that does not
   * exist is chrome pretending to be a control, and here it sat directly above
   * "No workspaces yet."
   *
   * Two things this must NOT be gated on, and both are the difference between a
   * gate and a trap:
   *   - NOT `visible.length`. Narrowing to zero would remove the box holding the
   *     query that did it, so the only way back would be to reload the page.
   *   - NOT `rows.length` alone. While loading, `rows` is empty, so the row
   *     would appear the instant the snapshot landed and shove the whole list
   *     down by 40px. The skeleton stands in for rows we expect to have.
   */
  const narrowable = loading || rows.length > 0;

  return (
    <div
      data-testid="fleet-tree"
      className={cn(
        // `w-full`, never a pixel width, in BOTH states — an earlier version
        // duplicated the docked aside's own width here, and that duplication is
        // exactly what let the two drift: the mobile `Sheet` renders this same
        // tree at close to the full viewport, and a fixed rail width pinned it
        // to the DESKTOP measure regardless, leaving empty space beside a
        // column that was still truncating as if it were the narrower one.
        // `w-full` has no number to drift — it is always exactly whatever the
        // immediate parent is (the aside on desktop, the sheet's full width on
        // mobile), by construction rather than by a second literal kept in sync
        // by hand. THE RAIL'S WIDTH LIVES IN `app-shell.tsx` AND NOWHERE ELSE.
        //
        // `gap-1.5` (6px) is the rail's one rhythm — 3x the `gap-0.5` this used
        // to run at, and the same value the footer in `app-sidebar.tsx` uses,
        // so the create action, the search row, the states and every workspace
        // row sit exactly one step apart all the way down. Symmetry is the
        // point: an even column is what lets a deliberately BIGGER step (the
        // footer's service line) read as a group break rather than as noise.
        "relative flex flex-1 flex-col gap-1.5 transition-[padding] duration-200",
        // `p-3` expanded, matching the footer — the one band of the rail that
        // was never reported as cramped, so it sets the measure for the rest
        // rather than a new number being invented. Collapsed keeps `px-2`,
        // which is not a gutter but an arithmetic fit: 8 + a 32px icon button
        // + 8 is exactly the 48px icon rail, and `px-3` would push it off
        // centre.
        collapsed
          ? "w-full overflow-hidden px-2 pt-1"
          : "w-full overflow-y-auto p-3",
      )}
    >
      <NewWorkspaceButton collapsed={collapsed} active={pathname === "/"} />

      <div
        aria-hidden={collapsed}
        inert={collapsed}
        className={cn(
          // Same `gap-1.5` as the root: this group is a layout wrapper for the
          // collapse fade, not a section, so the rhythm must not change when it
          // is crossed — the create action above and the search row below it
          // are one step apart exactly like two workspace rows are.
          "flex min-h-0 flex-1 flex-col gap-1.5 transition-opacity duration-150",
          collapsed && "pointer-events-none opacity-0",
        )}
      >
        {narrowable ? (
          <div className="flex items-center gap-1.5">
            <div className="min-w-0 flex-1">
              {/* Full accessible name stays on `aria-label`; the visible
                  placeholder drops to one word and the type shrinks to the
                  rail's own scale (`text-xs`, matching the rows below it)
                  rather than the vendored default `text-sm` — the height
                  floor (`h-8`, unchanged) is the accessibility contract; the
                  weight the box carries at that height is a call-site choice,
                  and this box is chrome instrumenting the list, not content. */}
              <ThreadListSearch
                value={filter.query}
                onValueChange={(query) => setFilter({ ...filter, query })}
                placeholder="Search"
                aria-label="Search workspaces"
                className="text-xs"
                data-testid="fleet-filter"
              />
            </div>
            <FleetFilterMenu
              filter={filter}
              onFilterChange={setFilter}
              facets={facets}
            />
          </div>
        ) : null}

        <ConnectionState phase={stream.phase} className="max-w-none" />

        {stream.error ? (
          <ErrorState
            className="max-w-none"
            title="Cannot reach the daemon"
            detail={stream.error.message}
            retrying={false}
            onRetry={stream.refetch}
          />
        ) : null}

        {loading ? <FleetSkeleton /> : null}

        {/* TWO different nothings, and the filtered one needs a way back out.
            A rail that has narrowed to nothing and offers no exit is a filter
            that has become a trap — the workspaces are still there, and the
            only thing standing between the reader and them is a control they
            may not remember touching. "Create a workspace" is the wrong door
            for someone whose search simply missed. */}
        {!loading && visible.length === 0 ? (
          <div
            className="flex flex-col items-start gap-2 px-2.5 py-4"
            data-testid="fleet-empty-rail"
          >
            <p className="text-sm text-content-tertiary">
              {filtering ? "No workspaces match." : "No workspaces yet."}
            </p>
            {filtering ? (
              <Button
                variant="outline"
                size="sm"
                className="h-7"
                onClick={() => setFilter(NO_FILTER)}
                data-testid="fleet-clear-filter-rail"
              >
                Clear the filter
              </Button>
            ) : null}
          </div>
        ) : null}

        {visible.map((row) => (
          <WorkspaceRow
            key={row.workspace.state.id}
            row={row}
            active={pathname === `/w/${row.workspace.state.id}`}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * The rail's primary action, in assistant-ui's own placement: first child of
 * the list, full width when expanded, an icon button when collapsed. The
 * tooltip only mounts while collapsed — with the label visible it would just
 * repeat it.
 *
 * It NAVIGATES to `/` rather than opening the create dialog. Starting a
 * workspace is the landing surface's whole job — a brief you type, with the
 * cascade's answers already on the controls under it — and a modal form asking
 * the same questions in fewer words was the older, narrower door to the same
 * verb. The dialog is still reachable as "More options" from that page, which
 * is where a form belongs relative to the composer it elaborates. Being a
 * destination, it also marks itself when it IS the route, exactly like the
 * workspace rows beneath it.
 */
function NewWorkspaceButton({
  collapsed,
  active,
}: {
  collapsed: boolean;
  active: boolean;
}): React.ReactNode {
  const button = (
    <Button
      asChild
      variant={active ? "secondary" : "ghost"}
      size="sm"
      className={cn(
        "h-8 justify-start overflow-hidden font-normal transition-all duration-200",
        collapsed
          ? "w-8 gap-0 px-2 has-[>svg]:px-2"
          : "w-full gap-2 px-2.5 has-[>svg]:px-2.5",
      )}
    >
      <Link href="/" aria-label="New workspace" data-testid="fleet-create-rail">
        <PlusIcon className="size-4" />
        <span
          className={cn(
            "overflow-hidden whitespace-nowrap transition-all duration-200",
            collapsed ? "max-w-0 opacity-0" : "max-w-32 opacity-100",
          )}
        >
          New workspace
        </span>
      </Link>
    </Button>
  );

  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        {collapsed ? (
          <TooltipContent side="right">New workspace</TooltipContent>
        ) : null}
      </Tooltip>
    </TooltipProvider>
  );
}

/**
 * One workspace. The row leads with its agent's mark — the fleet routinely
 * mixes vendors, and which agent is running is the first thing that decides
 * whether a row is the one you want.
 *
 * The single trailing glyph carries the most urgent thing true of this row:
 * attention first, otherwise how far along it is. A rail this narrow can
 * afford exactly one mark. It now also carries the row's agent-activity
 * state as a SECOND, redundant carrier (`activityHue`) — the glyph's shape
 * still says what happened, colour only says how loudly to look.
 *
 * TWO LINES, NOT THREE. The row shows ONE age — how long since it last did
 * anything — because that is the question a rail exists to answer, and it is
 * also the key these rows are SORTED by, so a right-aligned recency column
 * down the rail exposes the ordering rather than competing with it. Creation
 * time is real but it is never the reason you scan a rail, so it lives in the
 * row's tooltip beside the exact update time. Giving both equal weight would
 * have cost a third line on every row, on the surface with the least width in
 * the app.
 *
 * The age sits on the TITLE line rather than under the entities: the second
 * line is the typed pair, and a third token wedged in beside two glyphs is
 * where the density this rail has been tuned for would go.
 */
function WorkspaceRow({
  row,
  active,
}: {
  row: FleetRow;
  active: boolean;
}): React.ReactNode {
  const { state, phase } = row.workspace;
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editField, setEditField] = useState<WorkspaceEditField>("title");
  const agentState = agentStateOf(row.workspace);
  const AgentStateIcon = agentGlyph(agentState);
  const phaseMark = phase ? phaseGlyph(phase.phase, phase.blocked) : null;
  const rowMarkLabel = row.workspace.needs_attention
    ? `${agentLabel(agentState)} — needs attention`
    : phase
      ? `${phaseLabel(phase.phase)} — step ${phase.index + 1} of ${phase.total}`
      : agentLabel(agentState);
  const hue = activityHue(agentState);
  const updatedIso = lastActivityIso(row.workspace);

  const openEditor = (field: WorkspaceEditField): void => {
    setEditField(field);
    setMenuOpen(false);
    setDialogOpen(true);
  };

  const stopMenuButtonNavigation = (event: React.SyntheticEvent): void => {
    event.stopPropagation();
  };

  return (
    <>
      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <div
          className="group relative"
          data-testid="fleet-row"
          data-workspace-id={state.id}
          onContextMenu={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setMenuOpen(true);
          }}
        >
          <Button
            asChild
            variant={active ? "secondary" : "ghost"}
            size="sm"
            className="h-auto w-full justify-start py-2 pr-8 font-normal"
          >
            <Link
              href={`/w/${state.id}`}
              title={[
                `${state.title} — ${row.repoName} (${state.agent_name})`,
                `Created ${absoluteTime(state.created_at)}`,
                updatedIso ? `Updated ${absoluteTime(updatedIso)}` : null,
              ]
                .filter((line) => line !== null)
                .join("\n")}
            >
              <AgentMark agentName={state.agent_name} />
              <span className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
                <span className="flex w-full min-w-0 items-baseline gap-2">
                  {/* `text-sm`, RE-MEASURED against the CURRENT rail, not the one
                    this used to say. `Frontend UI migration` needs 128px at this
                    size, and the docked column was 123px when this was written —
                    against a 260px rail, since grown twice, to 392px. The tightest
                    realistic column here (icon, gap, a badge, the age column, all
                    present) is now ~228px even after the wider `p-3` gutter took
                    8px back — comfortably over. That puts the title a full ramp
                    step above the entity line beneath it, which is what actually
                    makes "project and branch smaller" true — pinning both to the
                    same `text-xs` left no size gap for
                    a 1px step to read at, so the only lever left was tier, not
                    size. Moving the TITLE up leaves the entity line free to stay
                    at the type floor and still read as visibly smaller. */}
                  <span className="min-w-0 flex-1 truncate text-sm text-content-primary">
                    {state.title}
                  </span>
                  {/* `max-w-16` and `truncate` are not defensive. Before mount this
                    renders the ABSOLUTE time — that is what makes it hydration-safe
                    — and an unbounded `8/10/2026, 3:12:07 PM` would crush the title
                    to nothing on every first paint. Capped, it clips for one frame
                    and then becomes "2h ago", which needs half the width. */}
                  <RelativeTime
                    iso={updatedIso}
                    className="text-content-tertiary max-w-16 shrink-0 truncate text-xs tabular-nums"
                  />
                </span>
                {/* Typed entities, so no middot — same treatment as the fleet card.
                  The glyphs already say where one ends and the next begins.
                  `PROJECT_MIN_WIDTH` on the project only: it is the identifying
                  half of the pair, so it keeps 8 characters before the branch
                  beside it may shrink further — see `entity.tsx` for why a
                  minimum, not a cap, is what lets this same row also fill the
                  mobile sheet's much wider column without clipping either name. */}
                <span className="text-content-tertiary flex w-full min-w-0 items-center gap-2 text-xs">
                  <ProjectLabel
                    name={row.repoName}
                    className={PROJECT_MIN_WIDTH}
                  />
                  <BranchLabel name={state.branch} />
                </span>
              </span>
              <span
                className={cn(
                  "inline-flex shrink-0 items-center gap-1",
                  hue ?? "text-muted-foreground",
                )}
                data-testid="fleet-row-mark"
                data-hue={hue ?? "neutral"}
                aria-label={rowMarkLabel}
                title={rowMarkLabel}
              >
                {phaseMark ? <span aria-hidden className="text-xs">{phaseMark}</span> : <AgentStateIcon aria-hidden className="size-3" />}
                <span className="sr-only">{rowMarkLabel}</span>
              </span>
            </Link>
          </Button>
          <DropdownMenuTrigger asChild>
            <TooltipIconButton
              tooltip="Workspace options"
              aria-label="Workspace options"
              className="absolute top-1/2 right-1 invisible -translate-y-1/2 group-focus-within:visible group-hover:visible"
              onClick={stopMenuButtonNavigation}
              onPointerDown={stopMenuButtonNavigation}
            >
              <MoreHorizontalIcon />
            </TooltipIconButton>
          </DropdownMenuTrigger>
        </div>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => openEditor("title")}>
            Rename…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openEditor("description")}>
            Edit description…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RenameWorkspaceDialog
        state={state}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initialFocus={editField}
      />
    </>
  );
}

/**
 * Loading rows, shaped like the real ones.
 *
 * Deliberately NOT `SidebarMenuSkeleton`: it randomizes each row's width with
 * `Math.random()`, so the server and the client never agree and React logs a
 * hydration mismatch on every cold load.
 */
function FleetSkeleton(): React.ReactNode {
  return (
    <div
      className="flex flex-col gap-1.5"
      role="status"
      aria-label="Loading workspaces"
    >
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} className="flex h-12 items-center gap-2 px-2.5">
          <Skeleton className="size-4 shrink-0" />
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-2.5 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}
