"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import { MessageCircleIcon } from "lucide-react";

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { FleetFilterMenu } from "./fleet-filter";
import type { FleetFacets, FleetFilter } from "./filter";
import type { FleetRow } from "./types";

export interface FleetSearchController {
  readonly open: boolean;
  readonly filter: FleetFilter;
  readonly allRows: readonly FleetRow[];
  readonly scopedRows: readonly FleetRow[];
  readonly visibleRows: readonly FleetRow[];
  readonly facets: FleetFacets;
  setOpen(open: boolean): void;
  setFilter(filter: FleetFilter): void;
}

/**
 * The input band's scale, reached the way the vendored `CommandDialog` reaches
 * it: `CommandInput` hard-codes its wrapper's `h-9`, `gap-2` and `size-4` mark
 * on a div it renders itself, so the only seam upstream leaves is a slot
 * selector from the `Command` root. Everything with a real `className` prop
 * (the input, the list, the group, the rows) is scaled at its own call site.
 *
 * Type moves by RAMP STEP and never by a px literal (§1), so the overlay still
 * answers to the density root, browser zoom and a reader's own font size. The
 * filter trigger deliberately does not scale with it: it is a pointer target
 * already sitting on its 24px floor, and size and hit area are separate
 * decisions.
 */
const PALETTE_SCALE = [
  "**:data-[slot=command-input-wrapper]:h-12",
  "**:data-[slot=command-input-wrapper]:gap-3",
  "[&_[data-slot=command-input-wrapper]_svg]:size-5",
].join(" ");

/** Search and open a scoped, already-filtered workspace without changing the current page first. */
export function FleetPalette({
  search,
  onOpenChange,
  onCloseAutoFocus,
}: {
  search: FleetSearchController;
  onOpenChange(open: boolean): void;
  onCloseAutoFocus(): void;
}): React.ReactNode {
  const router = useRouter();
  const commands = useMemo(
    () =>
      search.visibleRows.map((row) => ({
        id: row.workspace.state.id,
        label: row.workspace.state.title,
        group: row.repoName,
      })),
    [search.visibleRows],
  );


  const jump = (id: string): void => {
    search.setOpen(false);
    router.push(`/w/${id}`);
  };

  return (
    <Dialog open={search.open} onOpenChange={onOpenChange}>
      <DialogContent
        className="overflow-hidden p-0 sm:max-w-2xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onCloseAutoFocus();
        }}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>Search workspaces</DialogTitle>
        </DialogHeader>
        <div data-testid="workspace-search-dialog">
          <Command shouldFilter={false} className={PALETTE_SCALE}>
            <div className="flex min-w-0 items-center border-b pr-10">
              <div className="min-w-0 flex-1">
              <CommandInput
                className="min-w-0 h-12 text-base"
                value={search.filter.query}
                onValueChange={(query) => search.setFilter({ ...search.filter, query })}
                placeholder="Search workspaces"
                aria-label="Search workspaces"
              />
              </div>
              <FleetFilterMenu
                variant="sidebar"
                filter={search.filter}
                onFilterChange={search.setFilter}
                facets={search.facets}
              />
            </div>
            {/* Taller rows in the same 300px well would show fewer workspaces
                than before. The scale step, not an arbitrary px: upstream's
                `max-h-[300px]` is the one bound here that ignores the density
                root, and a scanned list is exactly what `max-h-*` is for. */}
            <CommandList className="max-h-112">
              {/* `CommandEmpty` takes no `className` of its own and spreads props
                  over a hard-coded one, so this REPLACES rather than merges and
                  has to restate the centering it is scaling. */}
              {commands.length === 0 ? (
                <CommandEmpty className="py-8 text-center text-base">
                  No workspaces match. Clear the search or filters to see recent workspaces.
                </CommandEmpty>
              ) : null}
              <CommandGroup
                heading={search.filter.query.trim() === "" ? "Recent workspaces" : "Workspaces"}
                className="p-2 [&_[cmdk-group-heading]]:px-3 [&_[cmdk-group-heading]]:py-2 [&_[cmdk-group-heading]]:text-sm"
              >
                {commands.map((command) => (
                  <CommandItem
                    key={command.id}
                    value={command.id}
                    onSelect={() => jump(command.id)}
                    data-testid="fleet-search-result"
                    className="gap-3 px-3 py-2.5 text-base [&_svg:not([class*='size-'])]:size-5"
                  >
                    <MessageCircleIcon aria-hidden />
                    <span className="min-w-0 flex-1 truncate">{command.label}</span>
                    <span className="shrink-0 text-sm text-content-tertiary">{command.group}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </div>
      </DialogContent>
    </Dialog>
  );
}
