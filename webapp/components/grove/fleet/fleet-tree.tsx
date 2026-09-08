"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { MoreHorizontalIcon, PlusIcon, Trash2Icon } from "lucide-react";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { ConnectionState } from "@/components/elements/connection-state";
import { ErrorState } from "@/components/elements/error-state";
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
import { BRANCH_GLYPH } from "@/components/grove/entity";
import { LoopingText, MarqueePauseButton } from "@/components/grove/overflow-text";
import { AgentMark } from "@/components/grove/agent-mark";
import { WorkingMark } from "@/components/grove/working-loader";
import { absoluteTime } from "@/components/grove/relative-time";
import { KillDialog } from "@/components/grove/workspace/kill-dialog";
import { useWorkspaceActions } from "@/lib/grove/hooks";
import { FleetFilterMenu } from "./fleet-filter";
import { ProjectHeading } from "./project-heading";
import {
  ProjectContextPicker,
  projectContextLabel,
  projectLaunchHref,
  scopeFleetRows,
  type ProjectContextController,
} from "./project-context";
import { SessionMetadata, SessionMetricDetails } from "./workspace-metrics";
import {
  RenameWorkspaceDialog,
  type WorkspaceEditField,
} from "./rename-workspace-dialog";
import {
  activeFilterCount,
  agentStateOf,
  filterRows,
  fleetGroups,
  lastActivityIso,
  type FleetFilter,
} from "./filter";
import {
  agentGlyph,
  agentLabel,
  attentionLabel,
  PHASE_BLOCKED_ICON,
  phaseGlyph,
  phaseLabel,
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
  const projects = stream.snapshot?.projects ?? [];
  const rows = search.scopedRows;
  const visible = search.visibleRows;
  const groupBy = project.context.kind === "all" ? search.filter.groupBy : "none";
  const groups = useMemo(() => fleetGroups(visible, groupBy), [visible, groupBy]);
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

        {outsideContext && !loading ? (
          <div className="flex flex-wrap items-center gap-1 text-xs text-content-tertiary" data-testid="rail-outside-context">
            <span>Open workspace is outside this view.</span>
            <Button variant="ghost" size="sm" className="min-h-[28px] px-1" onClick={showCurrentProject}>
              Show its project
            </Button>
          </div>
        ) : null}

        {loading ? <FleetSkeleton /> : null}

        {!loading && visible.length === 0 ? (
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
            {group.rows.map((row) => (
              <WorkspaceRow
                key={row.workspace.state.id}
                row={row}
                active={pathname === `/w/${row.workspace.state.id}`}
                grouped={search.filter.groupBy === "project"}
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

const PHASE_MARK_TONE = {
  progress: "text-content-tertiary",
  done: "text-success",
  blocked: "text-warning",
} as const;

const ROW_RESTING = "border-surface-edge hover:border-edge-control";
const ROW_SELECTED = "border-primary hover:border-primary focus-visible:border-primary";
const ROW_STATES = cn(
  "bg-transparent",
  "hover:bg-surface-base dark:hover:bg-surface-base",
  "active:bg-accent dark:active:bg-accent",
  "focus-visible:ring-0 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
);

/** One workspace navigation card. */
function WorkspaceRow({
  row,
  active,
  grouped,
}: {
  row: FleetRow;
  active: boolean;
  grouped: boolean;
}): React.ReactNode {
  const { state, phase } = row.workspace;
  const router = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [killing, setKilling] = useState(false);
  const [editField, setEditField] = useState<WorkspaceEditField>("title");
  const { kill } = useWorkspaceActions(state.id);
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
        <CardShell
          className={cn(
            "group relative min-w-0 border has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring",
            active ? ROW_SELECTED : cn(ROW_RESTING, "opacity-95 hover:opacity-100 focus-within:opacity-100"),
          )}
          data-testid="fleet-row"
          data-workspace-id={state.id}
          data-selected={active ? "true" : "false"}
          onContextMenu={(event) => {
            event.preventDefault();
            event.stopPropagation();
            setMenuOpen(true);
          }}
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <Link
                href={`/w/${state.id}`}
                aria-current={active ? "page" : undefined}
                aria-label={`${state.title}. ${row.repoName}. ${state.agent_name}.`}
                className={cn("block min-w-0 focus-visible:outline-none", ROW_STATES)}
              >
                {/* `pr-11`, not `pr-10`: the options button is 28px wide and
                    sits at `right-1.5` (6px), so it occupies the last 34px —
                    `pr-10` reserved 32 and was always 2px short. Nothing had
                    landed in that gap until the working mark became the last
                    item on the line, which is how a latent off-by-two became
                    visible. Measured, not computed from the class names. */}
                <header className="surface-header flex min-h-[28px] min-w-0 items-center gap-2 px-2.5 py-1 pr-11 [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:pr-14">
                  <AgentMark agentName={state.agent_name} className="size-5 shrink-0" />
                  <LoopingText className="min-w-0 flex-1 text-base font-medium text-content-primary">
                    {state.title}
                  </LoopingText>
                  {/* LAST in the title band, so it reads as a property of this
                      row's name rather than of the marks on the line below —
                      and it is the one mark here that says something is
                      happening RIGHT NOW, which is why it moves and they do
                      not. It sits inside the header's own `pr-10`, so it can
                      never collide with the options button that reserves that
                      corner. Rendered only while working: an absent claim takes
                      no space (design system §7). */}
                  {agentState === "working" ? (
                    <WorkingMark className="shrink-0" />
                  ) : null}
                  {!grouped ? <span className="sr-only">Project: {row.repoName}</span> : null}
                </header>
                <div className="flex min-w-0 flex-col gap-1 px-2.5 py-2 text-sm text-content-secondary">
                  <SessionMetadata
                    workspace={row.workspace}
                    context={
                      <div className="flex min-w-0 items-center gap-2" data-testid="rail-context">
                        <span className="flex min-w-0 flex-[0_1_auto] items-center gap-1" data-testid="rail-branch">
                          <BRANCH_GLYPH aria-hidden className="size-3 shrink-0" />
                          <span className="sr-only">Branch: </span>
                          <LoopingText className="min-w-0">{state.branch}</LoopingText>
                        </span>
                        {attentionMarkLabel && attentionText ? (
                          <span
                            className="inline-flex shrink-0 items-center gap-1 text-destructive"
                            data-testid="fleet-row-attention-mark"
                            aria-label={attentionMarkLabel}
                          >
                            <AttentionIcon aria-hidden className="size-3 shrink-0" />
                            <span>{attentionText}</span>
                          </span>
                        ) : null}
                        {PhaseIcon && phaseMarkLabel && phaseText ? (
                          <span
                            className={cn(
                              "inline-flex shrink-0 items-center gap-1",
                              phase?.blocked
                                ? PHASE_MARK_TONE.blocked
                                : phase?.phase === "done"
                                  ? PHASE_MARK_TONE.done
                                  : PHASE_MARK_TONE.progress,
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
                          </span>
                        ) : null}
                      </div>
                    }
                  />
                </div>
              </Link>
            </TooltipTrigger>
            <TooltipContent side="left" sideOffset={8}>
              <p>{state.title} — {row.repoName} ({state.agent_name})</p>
              <p>Branch: {state.branch}</p>
              {attentionMarkLabel ? <p>{attentionMarkLabel}</p> : null}
              {phaseMarkLabel ? <p>{phaseMarkLabel}</p> : null}
              <SessionMetricDetails workspace={row.workspace} />
              <p>Created {absoluteTime(state.created_at)}</p>
              {updatedIso ? <p>Updated {absoluteTime(updatedIso)}</p> : null}
            </TooltipContent>
          </Tooltip>
          {active ? (
            <span
              aria-hidden
              data-testid="fleet-row-marker"
              className="pointer-events-none absolute top-2 bottom-2 left-0 w-0.5 bg-primary"
            />
          ) : null}
          <DropdownMenuTrigger asChild>
            <TooltipIconButton
              tooltip="Workspace options"
              aria-label="Workspace options"
              className="invisible absolute top-px right-1.5 min-h-[28px] min-w-[28px] [@media(pointer:coarse)]:visible [@media(pointer:coarse)]:min-h-11 [@media(pointer:coarse)]:min-w-11 group-focus-within:visible group-hover:visible"
              onClick={stopMenuButtonNavigation}
              onPointerDown={stopMenuButtonNavigation}
            >
              <MoreHorizontalIcon />
            </TooltipIconButton>
          </DropdownMenuTrigger>
        </CardShell>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={() => openEditor("title")}>
            Rename…
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openEditor("description")}>
            Edit description…
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
                if (active) router.push("/");
              },
            },
          )
        }
      />
    </>
  );
}

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
          <div className="surface-header flex items-center gap-2 px-2.5 py-1">
            <Skeleton className="size-5 shrink-0" />
            <Skeleton className="h-5 w-2/3" />
          </div>
          <div className="flex min-w-0 flex-col gap-1 px-2.5 py-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-full" />
          </div>
        </CardShell>
      ))}
    </div>
  );
}
