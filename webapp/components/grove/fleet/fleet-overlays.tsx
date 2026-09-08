"use client";

import type { DashboardSnapshotView } from "@/lib/grove/api";

import { CreateWorkspaceDialog } from "./create-workspace-dialog";
import { useCreateWorkspaceUi } from "./create-store";
import { FleetPalette, type FleetSearchController } from "./fleet-palette";

/** The shell's single search overlay and the existing create dialog. */
export function FleetOverlays({
  snapshot,
  search,
  onSearchOpenChange,
  onSearchCloseAutoFocus,
}: {
  snapshot: DashboardSnapshotView | undefined;
  search: FleetSearchController;
  onSearchOpenChange(open: boolean): void;
  onSearchCloseAutoFocus(): void;
}): React.ReactNode {
  const open = useCreateWorkspaceUi((state) => state.open);
  const repoRoot = useCreateWorkspaceUi((state) => state.repoRoot);
  const setOpen = useCreateWorkspaceUi((state) => state.setOpen);
  const setRepoRoot = useCreateWorkspaceUi((state) => state.setRepoRoot);
  const firstRepoRoot = snapshot?.projects[0]?.repo_root ?? "";

  return (
    <>
      <FleetPalette
        search={search}
        onOpenChange={onSearchOpenChange}
        onCloseAutoFocus={onSearchCloseAutoFocus}
      />
      <CreateWorkspaceDialog
        open={open}
        onOpenChange={setOpen}
        repoRoot={repoRoot || firstRepoRoot}
        onRepoRootChange={setRepoRoot}
        projects={snapshot?.projects ?? []}
      />
    </>
  );
}
