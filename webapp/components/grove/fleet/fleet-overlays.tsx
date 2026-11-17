"use client";

import { MoreOptionsDialog } from "@/components/grove/launch/more-options-dialog";
import { FleetPalette, type FleetSearchController } from "./fleet-palette";

/** The shell's single search overlay and the More options create dialog. */
export function FleetOverlays({
  search,
  onSearchOpenChange,
  onSearchCloseAutoFocus,
}: {
  search: FleetSearchController;
  onSearchOpenChange(open: boolean): void;
  onSearchCloseAutoFocus(): void;
}): React.ReactNode {
  return (
    <>
      <FleetPalette
        search={search}
        onOpenChange={onSearchOpenChange}
        onCloseAutoFocus={onSearchCloseAutoFocus}
      />
      <MoreOptionsDialog />
    </>
  );
}
