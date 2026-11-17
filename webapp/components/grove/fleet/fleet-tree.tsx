"use client";

import { memo, useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ExternalLinkIcon, FileTextIcon, GitForkIcon, MoreHorizontalIcon, PencilIcon, PinIcon, PinOffIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { useSidebarPins, useSetSidebarPin } from "@/lib/grove/hooks/sidebar-pins";
import { pinsFirst } from "./sidebar-pins";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ConnectionState } from "@/components/elements/connection-state";
import { ErrorState } from "@/components/elements/error-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { CardShell } from "@/components/grove/card";
import { LoopingText, MarqueePauseButton } from "@/components/grove/overflow-text";
import { AgentMark } from "@/components/grove/agent-mark";
import { WorkingMark } from "@/components/grove/working-loader";
import { KillDialog } from "@/components/grove/workspace/kill-dialog";
import { useWorkspaceActions } from "@/lib/grove/hooks";
import { replyCount } from "@/lib/grove/adapters";
import { useHasUnread } from "./unread";
import { FleetFilterMenu } from "./fleet-filter";
import { ProjectHeading } from "./project-heading";
import {
  ProjectContextPicker,
  projectContextLabel,
  projectLaunchHref,
  workspaceLaunchHref,
  scopeFleetRows,
  type ProjectContextController,
} from "./project-context";
import { SessionMetadata, UnreadDot } from "./workspace-metrics";
import { SessionOverview } from "./session-overview";
import { OVERVIEW_COLLISION_PADDING, overviewAlign, type OverviewAlign } from "./overview-placement";
import {
  RenameWorkspaceDialog,
  type WorkspaceEditField,
} from "./rename-workspace-dialog";
import {
  activeFilterCount,
  agentStateOf,
  filterRows,
  fleetGroups,
  type FleetFilter,
} from "./filter";
import {
  agentGlyph,
  agentLabel,
  attentionLabel,
  PHASE_BLOCKED_ICON,
  phaseGlyph,
  phaseLabel,
  railPillAccent,
} from "./tokens";
import type { FleetSearchController } from "./fleet-palette";
import type { FleetRow } from "./types";
import type { FleetStream } from "./use-fleet";

/** The scoped rail list. Its search/filter controller belongs to the one AppShell that owns both rail mounts. */
export function FleetTree({
  collapsed,
  stream,
  project,
  search,
}: {
  collapsed: boolean;
  stream: FleetStream;
  project: ProjectContextController;
  search: FleetSearchController;
}): React.ReactNode {
  const pathname = usePathname();
  const pins = useSidebarPins();
  const setPin = useSetSidebarPin();
  const onPinChange = useCallback((id: string, pinned: boolean) => {
    setPin.mutate({ id, pinned });
  }, [setPin.mutate]);
  const projects = stream.snapshot?.projects ?? [];
  const rows = search.scopedRows;
  const workers = search.visibleRows;
  const groupBy = project.context.kind === "all" ? search.filter.groupBy : "none";
  const groups = useMemo(() => fleetGroups(workers, groupBy), [workers, groupBy]);
  const currentRow = search.allRows.find((row) => pathname === `/w/${row.workspace.state.id}`);
  const outsideContext = currentRow && project.context.kind !== "all" &&
    !rows.some((row) => row.workspace.state.id === currentRow.workspace.state.id);
  const showCurrentProject = (): void => {
    const owner = projects.find((candidate) => candidate.workspaces.some(
      (workspace) => workspace.state.id === currentRow?.workspace.state.id,
    ));
    if (owner) project.selectProject(owner.cwd);
  };

  const loading = stream.isPending && stream.snapshot === undefined;
  const filtering = search.filter.query.trim() !== "" || activeFilterCount(search.filter) > 0;

  return (
    <div
      data-testid="fleet-tree"
      className={cn(
        "relative flex flex-1 flex-col gap-3 transition-[padding] duration-200",
        collapsed
          ? "w-full overflow-hidden px-2 pt-1 [@media(pointer:coarse)]:px-0.5"
          : "w-full overflow-y-auto p-3",
      )}
    >
      <div hidden={collapsed}>
        <ProjectContextPicker
          projects={projects}
          context={project.context}
          onSelect={project.selectProject}
          status={stream.error ? "error" : loading ? "loading" : "ready"}
        />
      </div>
      <div className="flex items-center gap-1.5">
        <NewWorkspaceButton collapsed={collapsed} href={projectLaunchHref(project.context)} />
        {collapsed ? null : (
          <>
            <FleetFilterMenu
              variant="sidebar"
              filter={search.filter}
              onFilterChange={search.setFilter}
              facets={search.facets}
            />
            <MarqueePauseButton className="size-6 min-h-[24px] min-w-[24px] [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11" />
          </>
        )}
      </div>

      {/* Keep the list intrinsic-height so its overflow cannot bypass the scroller's bottom padding. */}
      <div
        aria-hidden={collapsed}
        inert={collapsed}
        className={cn(
          "flex shrink-0 flex-col gap-3 transition-opacity duration-150",
          collapsed && "pointer-events-none opacity-0",
        )}
      >
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

        {pins.error ? (
          <ErrorState
            className="max-w-none"
            title="Cannot load session pins"
            detail={pins.error.message}
            retrying={pins.isFetching}
            onRetry={() => { void pins.refetch(); }}
          />
        ) : null}

        {outsideContext && !loading ? (
          <div className="flex flex-wrap items-center gap-1 text-xs text-content-tertiary" data-testid="rail-outside-context">
            <span>Open workspace is outside this view.</span>
            <Button variant="ghost" size="sm" className="min-h-[28px] px-1" onClick={showCurrentProject}>
              Show its project
            </Button>
          </div>
        ) : null}

        {loading ? <FleetSkeleton /> : null}

        {!loading && workers.length === 0 ? (
          <div
            className="flex flex-col items-start gap-2 px-2.5 py-4"
            data-testid="fleet-empty-rail"
          >
            <p className="text-sm text-content-tertiary">
              {project.context.kind === "missing"
                ? "This project is no longer available."
                : filtering ? "No workspaces match." : "No workspaces yet."}
            </p>
            {project.context.kind === "missing" ? (
              <Button variant="outline" size="sm" onClick={() => project.selectProject(null)}>
                All projects
              </Button>
            ) : null}
            {filtering ? (
              <Button
                variant="outline"
                size="sm"
                className="h-7"
                onClick={() => search.setFilter({ ...search.filter, query: "", attentionOnly: false, hiddenStates: [], hiddenProjects: [] })}
                data-testid="fleet-clear-filter-rail"
              >
                Clear the filter
              </Button>
            ) : null}
          </div>
        ) : null}

        {groups.map((group, index) => (
          <section key={group.key} aria-label={group.repoName ? `${group.repoName} workspaces` : "Recent workspaces"} className="flex flex-col gap-3" data-testid="fleet-rail-group">
            {/* A heading needs more room ABOVE it than below, or it reads as
                belonging to the group it follows. This stack had the opposite:
                measured at the 80% density root, a separator with `my-1.5`
                between two `gap-3` section gaps put 34.6px above the label and
                14.4px below it, so every project name floated between groups
                rather than titling the one under it. The rule is also
                redundant now that `--border` is visible — a line plus 20px of
                padding is two separators doing one job. */}
            {group.repoName ? (
              <div className={cn("px-2.5 pb-1.5", index > 0 ? "pt-2" : "pt-0.5")}>
                <ProjectHeading name={group.repoName} count={group.rows.length} />
              </div>
            ) : null}
            {pinsFirst(group.rows, pins.data ?? []).map((row) => (
              <WorkspaceRow
                key={row.workspace.state.id}
                row={row}
                active={pathname === `/w/${row.workspace.state.id}`}
                launchHref={workspaceLaunchHref(projects, row.workspace.state.id) ?? "/"}
                grouped={search.filter.groupBy === "project"}
                pinned={pins.data?.includes(row.workspace.state.id) ?? false}
                pinDisabled={pins.data === undefined || setPin.isPending}
                onPinChange={onPinChange}
              />
            ))}
          </section>
        ))}
      </div>
    </div>
  );
}

/** The rail's primary destination is the landing composer, scoped when one project is selected. */
function NewWorkspaceButton({
  collapsed,
  href,
}: {
  collapsed: boolean;
  href: string;
}): React.ReactNode {
  const button = (
    <Button
      asChild
      variant="outline"
      size="sm"
      className={cn(
        "bg-transparent dark:bg-transparent border-edge-control h-6 min-h-[24px] justify-start overflow-hidden font-medium transition-all duration-200 [@media(pointer:coarse)]:min-h-11",
        collapsed
          ? "w-6 min-w-[24px] justify-center gap-0 p-1 has-[>svg]:px-1 [@media(pointer:coarse)]:min-w-11"
          : "min-w-0 flex-1 gap-2 px-2.5 has-[>svg]:px-2.5",
      )}
    >
      <Link href={href} aria-label="New workspace" data-testid="fleet-create-rail">
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
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      {collapsed ? <TooltipContent side="right">New workspace</TooltipContent> : null}
    </Tooltip>
  );
}

/**
 * The rail's pill geometry: the fleet badge density with room for the blocked
 * flag, which sits just outside its glyph's box and the badge would clip.
 */
const RAIL_PILL = "h-5 gap-1 overflow-visible px-1.5 py-0 [&>svg]:size-3";

const ROW_RESTING = "border-surface-edge hover:border-edge-control";
const ROW_SELECTED = "border-edge-control";
// Fills live on `.rail-row` in globals.css as one interaction ladder (rest →
// hover → selected), never keyed on what a row reports; the border steps with
// it. The link stays transparent so it cannot paint over the card's fill.
const ROW_STATES = cn(
  "bg-transparent",
  "active:bg-accent dark:active:bg-accent",
  "focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
);

/** One workspace navigation card. */
const WorkspaceRow = memo(function WorkspaceRow({
  row,
  active,
  launchHref,
  grouped,
  pinned,
  pinDisabled,
  onPinChange,
}: {
  row: FleetRow;
  active: boolean;
  /** A new workspace in this row's project — where deleting the open one lands. */
  launchHref: string;
  grouped: boolean;
  pinned: boolean;
  pinDisabled: boolean;
  onPinChange(id: string, pinned: boolean): void;
}): React.ReactNode {
  const { state, phase } = row.workspace;
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [killing, setKilling] = useState(false);
  const [editField, setEditField] = useState<WorkspaceEditField>("title");
  // Read where the card sits on the same event Radix opens on, so the panel
  // grows toward the viewport's middle. `pointerenter` is not enough: a pointer
  // already resting on a card after a navigation or scroll never re-enters it,
  // and the panel then opened centred against a stale default. `setAlign` bails
  // out when the edge is unchanged, so a move within a card costs no render.
  const [align, setAlign] = useState<OverviewAlign>("center");
  const placeOverview = (event: React.PointerEvent | React.FocusEvent): void => {
    const card = event.currentTarget.getBoundingClientRect();
    setAlign(overviewAlign(card.top + card.height / 2, window.innerHeight));
  };
  const { kill, fork } = useWorkspaceActions(state.id);
  const agentState = agentStateOf(row.workspace);
  const AttentionIcon = agentGlyph(agentState);
  const PhaseIcon = phase ? phaseGlyph(phase.phase) : null;
  const attentionMarkLabel = row.workspace.needs_attention ? attentionLabel(agentState) : undefined;
  const phaseMarkLabel = phase
    ? phase.blocked
      ? `${phaseLabel(phase.phase)} — blocked: ${phase.note?.trim() || "No reason reported"}`
      : `${phaseLabel(phase.phase)} — step ${phase.index + 1} of ${phase.total}`
    : undefined;
  const phaseText = phase ? phaseLabel(phase.phase) : undefined;
  const attentionText = row.workspace.needs_attention ? agentLabel(agentState) : undefined;
  const unread = useHasUnread(state.id, replyCount(row.workspace)) && !active;

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
        <CardShell
          className={cn(
            "rail-row group relative min-w-0 border has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring",
            active ? ROW_SELECTED : ROW_RESTING,
          )}
          data-testid="fleet-row"
          data-workspace-id={state.id}
          data-selected={active ? "true" : "false"}
          data-pinned={pinned ? "true" : "false"}
          onContextMenu={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setMenuOpen(true);
          }}
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <Link
                onPointerMove={placeOverview}
                onFocus={placeOverview}
                href={`/w/${state.id}`}
                aria-current={active ? "page" : undefined}
                aria-label={`${state.title}.${pinned ? " Pinned session." : ""}${unread ? " New responses." : ""} ${row.repoName}. ${state.agent_name}.`}
                className={cn("flex min-w-0 items-start gap-3 p-3 focus-visible:outline-none", ROW_STATES)}
              >
                {/* The agent's mark on its own sunken tile, spanning the title
                    and context lines: identity is read before the name, and a
                    tile is what lets a row be found by its leading column
                    alone. The tile is neutral on purpose — the agent brand is
                    the only identity the wire carries, and a hue picked per
                    row would be a colour meaning nothing (design system §4.1). */}
                <span aria-hidden className="rail-agent-tile flex size-10 shrink-0 items-center justify-center" data-testid="rail-agent-tile">
                  <AgentMark agentName={state.agent_name} className="size-5" />
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm text-content-secondary">
                  {/* `pr-9` reserves the options button's corner: 28px at
                      `right-1.5` is the card's last 34px, and the link's own
                      `p-3` already covers ~10 of those. Measured, not computed
                      from the class names. */}
                  {/* The title line holds the name and nothing else, so the
                      whole width left of the options corner is the name's —
                      a long title loops through it rather than truncating. */}
                  <header className="flex min-h-[28px] min-w-0 items-center gap-2 pr-9 [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:pr-11">
                    <LoopingText className="min-w-0 flex-1 text-base font-medium text-content-primary">
                      {state.title}
                    </LoopingText>
                    {pinned ? <PinIcon aria-hidden className="size-3.5 shrink-0" /> : null}
                    {!grouped ? <span className="sr-only">Project: {row.repoName}</span> : null}
                  </header>
                  <SessionMetadata
                    workspace={row.workspace}
                    context={
                      <div className="flex min-w-0 items-center gap-2" data-testid="rail-context">
                        {attentionMarkLabel && attentionText ? (
                          <Badge
                            variant="outline"
                            className={cn(RAIL_PILL, railPillAccent("attention"))}
                            data-testid="fleet-row-attention-mark"
                            aria-label={attentionMarkLabel}
                          >
                            <AttentionIcon aria-hidden className="shrink-0" />
                            <span>{attentionText}</span>
                          </Badge>
                        ) : null}
                        {PhaseIcon && phaseMarkLabel && phaseText ? (
                          <Badge
                            variant="outline"
                            className={cn(
                              RAIL_PILL,
                              railPillAccent(
                                phase?.blocked ? "blocked" : phase?.phase === "handoff" ? "done" : "progress",
                              ),
                            )}
                            data-testid="fleet-row-phase-mark"
                            aria-label={phaseMarkLabel}
                          >
                            <span aria-hidden className="relative inline-flex size-3 shrink-0 items-center justify-center">
                              <PhaseIcon className="size-3" />
                              {phase?.blocked ? (
                                <PHASE_BLOCKED_ICON className="absolute -right-1 -bottom-1 size-2 text-warning" />
                              ) : null}
                            </span>
                            <span>{phaseText}</span>
                          </Badge>
                        ) : null}
                        {/* LAST in the context row: the claims say what the
                            workspace IS, the mark says something is happening
                            RIGHT NOW, so it follows them. Rendered only while
                            working — an absent claim takes no space (§7). */}
                        {agentState === "working" ? (
                          <WorkingMark className="shrink-0" />
                        ) : null}
                      </div>
                    }
                  />
                </div>
              </Link>
            </TooltipTrigger>
            <TooltipContent
              side="right"
              align={align}
              sideOffset={12}
              collisionPadding={OVERVIEW_COLLISION_PADDING}
              className="session-overview-tooltip"
            >
              <SessionOverview workspace={row.workspace} unread={unread} />
            </TooltipContent>
          </Tooltip>
          {/* The left edge carries at most one claim, and selection wins:
              where you ARE outranks what a row reports. A handed-off row takes
              the success edge so finished work reads down the column without
              opening a tooltip; every other state keeps its word in the pills. */}
          {active ? (
            <span
              aria-hidden
              data-testid="fleet-row-marker"
              className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-primary"
            />
          ) : phase?.phase === "handoff" && !phase.blocked ? (
            <span
              aria-hidden
              data-testid="fleet-row-done-edge"
              className="pointer-events-none absolute inset-y-0 left-0 w-1 bg-success"
            />
          ) : null}
          {/* The unread cue: new model responses since this browser last had
              the workspace open (#919). A mark in the corner, never a rank —
              `sortedFleetRows` reads nothing from the unread store. */}
          {unread ? (
            <span aria-hidden className="pointer-events-none absolute top-1.5 left-1.5 flex" data-testid="fleet-row-unread">
              <UnreadDot />
            </span>
          ) : null}
          {/* Visible at rest, in the quiet tier: an empty title corner that
              only fills on hover read as a card missing a piece, and touch
              already had to show it. */}
          <DropdownMenuTrigger asChild>
            <TooltipIconButton
              tooltip="Workspace options"
              aria-label="Workspace options"
              className="absolute top-2.5 right-1.5 min-h-[28px] min-w-[28px] text-content-tertiary hover:text-content-primary [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11"
              onClick={stopMenuButtonNavigation}
              onPointerDown={stopMenuButtonNavigation}
            >
              <MoreHorizontalIcon />
            </TooltipIconButton>
          </DropdownMenuTrigger>
        </CardShell>
        <DropdownMenuContent align="end">
          <DropdownMenuItem asChild>
            <a href={`/w/${state.id}`} target="_blank" rel="noopener noreferrer">
              <ExternalLinkIcon aria-hidden />
              Open in new tab
            </a>
          </DropdownMenuItem>
          <DropdownMenuItem disabled={pinDisabled} onSelect={() => onPinChange(state.id, !pinned)}>
            {pinned ? <PinOffIcon aria-hidden /> : <PinIcon aria-hidden />}
            {pinned ? "Unpin session" : "Pin session"}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => openEditor("title")}>
            <PencilIcon aria-hidden />
            Rename…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openEditor("description")}>
            <FileTextIcon aria-hidden />
            Edit description…
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={fork.isPending}
            onSelect={() =>
              fork.mutate(undefined, { onSuccess: (created) => router.push(`/w/${created.id}`) })
            }
            data-testid="fleet-row-fork"
          >
            <GitForkIcon aria-hidden />
            Fork conversation
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            onSelect={() => {
              setMenuOpen(false);
              setKilling(true);
            }}
            data-testid="fleet-row-delete"
          >
            <Trash2Icon aria-hidden />
            Delete…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RenameWorkspaceDialog
        state={state}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        initialFocus={editField}
      />
      <KillDialog
        state={state}
        open={killing}
        onOpenChange={setKilling}
        pending={kill.isPending}
        onConfirm={(deleteBranch) =>
          kill.mutate(
            { deleteBranch },
            {
              onSuccess: () => {
                setKilling(false);
                if (active) router.replace(launchHref);
              },
            },
          )
        }
      />
    </>
  );
// Flattening a new snapshot recreates row wrappers even when their contents are unchanged.
}, (previous, next) =>
  previous.row.workspace === next.row.workspace &&
  previous.row.repoName === next.row.repoName &&
  previous.row.repoRoot === next.row.repoRoot &&
  previous.active === next.active &&
  previous.launchHref === next.launchHref &&
  previous.grouped === next.grouped &&
  previous.pinned === next.pinned &&
  previous.pinDisabled === next.pinDisabled &&
  previous.onPinChange === next.onPinChange,
);

/** Loading rows follow the same card anatomy without shadcn's randomized sidebar skeleton. */
function FleetSkeleton(): React.ReactNode {
  return (
    <div
      className="flex flex-col gap-3"
      role="status"
      aria-label="Loading workspaces"
    >
      {Array.from({ length: 5 }, (_, index) => (
        <CardShell key={index} className="min-w-0">
          <div className="flex min-w-0 items-start gap-3 p-3">
            <Skeleton className="size-10 shrink-0" />
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-full" />
            </div>
          </div>
        </CardShell>
      ))}
    </div>
  );
}
