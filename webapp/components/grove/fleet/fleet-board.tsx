"use client";

import { useState } from "react";
import { CircleHelpIcon } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  KanbanBoard,
  KanbanBoardColumn,
  KanbanBoardColumnHeader,
  KanbanBoardColumnList,
  KanbanBoardColumnListItem,
  KanbanBoardColumnTitle,
  KanbanBoardExtraMargin,
  KanbanBoardProvider,
} from "@/components/ui/kanban";
import { cn } from "@/lib/utils";
import { SessionOverview } from "./session-overview";
import { replyCount } from "@/lib/grove/adapters";
import { useHasUnread } from "./unread";
import { COLUMN_KEYS, kanbanColumns, UNREPORTED_COLUMN, type ColumnKey, type Selection } from "./kanban";
import { overviewAlign, OVERVIEW_COLLISION_PADDING, type OverviewAlign } from "./overview-placement";
import { phaseGlyph, phaseLabel } from "./tokens";
import { WorkspaceCard } from "./workspace-card";
import type { FleetRow } from "./types";

/** Every column's word and mark, from the one phase table plus the unreported case. */
export function columnPresentation(key: ColumnKey): { label: string; Icon: React.ComponentType<{ className?: string }> } {
  return key === UNREPORTED_COLUMN
    ? { label: "Unreported", Icon: CircleHelpIcon }
    : { label: phaseLabel(key), Icon: phaseGlyph(key) };
}

/**
 * The fleet as a board whose columns are the agents' own phase claims.
 *
 * Cards do not drag. A column is a report the AGENT made, and a person moving
 * a card between columns would be Grove publishing a phase the agent never
 * claimed — onto the ticket's sticky comment, where strangers read it. The
 * vendored board's drag plumbing stays unwired on purpose; its provider is
 * mounted only for the column anatomy and the live region.
 *
 * Every card carries the rail row's own hover overview, placed by the same
 * rule, so the board and the rail teach one preview.
 */
export function FleetBoard({
  rows,
  selected,
  onToggleSelected,
  grouped,
  hideEmpty = true,
}: {
  rows: readonly FleetRow[];
  selected: Selection;
  onToggleSelected: (id: string) => void;
  grouped: boolean;
  /** Fold empty columns to a rail. The filter menu owns the switch. */
  hideEmpty?: boolean;
}): React.ReactNode {
  const columns = kanbanColumns(rows);
  // A reader can open one folded column without changing the switch — "show
  // me Verify anyway" is a glance, not a preference. Reset when rows arrive
  // in it, because then it is no longer empty and the fold does not apply.
  const [opened, setOpened] = useState<ReadonlySet<ColumnKey>>(new Set());
  return (
    <KanbanBoardProvider>
      <KanbanBoard className="h-full min-h-0 flex-1 items-stretch" data-testid="fleet-board">
        {columns.map(({ key, rows: held }) => {
          const { label, Icon } = columnPresentation(key);
          const collapsed = hideEmpty && held.length === 0 && !opened.has(key);
          return (
            <KanbanBoardColumn
              key={key}
              columnId={key}
              // Every column is a full-height vertical lane, folded or not —
              // that is what makes it a board rather than a row of cards. An
              // open column takes an equal share of the width (the vendor's
              // fixed `w-64` hid Handoff behind a scroll at 1600px); a folded
              // one keeps the lane at rail width. Cards stack; they never tile.
              className={cn("fleet-column h-full max-h-full", collapsed ? "" : "min-w-64 flex-1 basis-0")}
              data-testid="fleet-board-column"
              data-column={key}
              data-count={held.length}
              data-collapsed={collapsed ? "true" : "false"}
            >
              {collapsed ? (
                <button
                  type="button"
                  className="flex h-full w-full flex-col items-center gap-2 py-2 text-content-tertiary hover:text-content-primary"
                  onClick={() => setOpened((held) => new Set(held).add(key))}
                  aria-label={`${label}: ${held.length === 0 ? "empty" : `${held.length} workspaces`}. Open the column.`}
                  data-testid="fleet-board-expand"
                >
                  {/* The same count badge the open header wears, so a folded
                      lane reads as the same column at rest, not a different thing. */}
                  <span className="fleet-column-count inline-flex min-w-5 justify-center px-1.5 py-0.5 text-xs font-medium tabular-nums" data-testid="fleet-board-count">
                    {held.length}
                  </span>
                  <Icon aria-hidden className="size-3.5 shrink-0" />
                  <span className="fleet-column-vertical text-xs font-medium">{label}</span>
                </button>
              ) : (
                <>
                  <KanbanBoardColumnHeader>
                    <KanbanBoardColumnTitle columnId={key} className="gap-1.5">
                      <span className="fleet-column-pill inline-flex items-center gap-1.5 px-2 py-0.5 text-xs font-medium">
                        <Icon aria-hidden className="size-3.5 shrink-0" />
                        {label}
                      </span>
                      <span className="fleet-column-count inline-flex min-w-5 justify-center px-1.5 py-0.5 text-xs font-medium tabular-nums" data-testid="fleet-board-count">
                        {held.length}
                      </span>
                    </KanbanBoardColumnTitle>
                    {hideEmpty && held.length === 0 ? (
                      <button
                        type="button"
                        className="text-xs text-content-tertiary hover:text-content-primary"
                        onClick={() => setOpened((held) => { const next = new Set(held); next.delete(key); return next; })}
                        data-testid="fleet-board-fold"
                      >
                        Fold
                      </button>
                    ) : null}
                  </KanbanBoardColumnHeader>
                  <KanbanBoardColumnList className="flex min-h-0 flex-1 flex-col gap-2 px-2 py-1">
                    {held.length === 0 ? (
                      <li className="px-1 py-6 text-center text-xs text-content-tertiary" data-testid="fleet-board-empty-column">
                        {key === UNREPORTED_COLUMN ? "Every workspace has reported." : `Nothing in ${label.toLowerCase()}.`}
                      </li>
                    ) : (
                      held.map((row) => (
                        <BoardCard
                          key={row.workspace.state.id}
                          row={row}
                          selected={selected.has(row.workspace.state.id)}
                          onToggleSelected={onToggleSelected}
                          grouped={grouped}
                        />
                      ))
                    )}
                  </KanbanBoardColumnList>
                </>
              )}
            </KanbanBoardColumn>
          );
        })}
        <KanbanBoardExtraMargin />
      </KanbanBoard>
    </KanbanBoardProvider>
  );
}

/** The fleet card on the board, wearing the rail's hover overview. */
function BoardCard({
  row,
  selected,
  onToggleSelected,
  grouped,
}: {
  row: FleetRow;
  selected: boolean;
  onToggleSelected: (id: string) => void;
  grouped: boolean;
}): React.ReactNode {
  const id = row.workspace.state.id;
  const unread = useHasUnread(id, replyCount(row.workspace));
  // Same placement rule as the rail: read the card's position on the event
  // Radix opens on, so the panel grows toward the viewport's middle.
  const [align, setAlign] = useState<OverviewAlign>("center");
  const placeOverview = (event: React.PointerEvent | React.FocusEvent): void => {
    const card = event.currentTarget.getBoundingClientRect();
    setAlign(overviewAlign(card.top + card.height / 2, window.innerHeight));
  };
  return (
    <KanbanBoardColumnListItem
      cardId={id}
      className="fleet-board-card list-none border-0 p-0"
      data-testid="fleet-board-card"
      data-blocked={row.workspace.phase?.blocked ? "true" : "false"}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            onPointerMove={placeOverview}
            onFocus={placeOverview}
            className={cn("relative", selected && "outline-2 outline-offset-2 outline-primary")}
            data-selected={selected ? "true" : "false"}
            data-testid="fleet-board-overview-trigger"
          >
            <WorkspaceCard
              workspace={row.workspace}
              repoName={row.repoName}
              grouped={grouped}
              selected={selected}
              onSelectedChange={onToggleSelected}
            />
          </div>
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
    </KanbanBoardColumnListItem>
  );
}

export { COLUMN_KEYS };
