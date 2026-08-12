"use client";

import { useMemo } from "react";

import type { DashboardSnapshotView } from "@/lib/grove/api";

import { CreateWorkspaceDialog } from "./create-workspace-dialog";
import { useCreateWorkspaceUi } from "./create-store";
import { fleetRows } from "./filter";
import { FleetPalette } from "./fleet-palette";

/**
 * The fleet's two app-level overlays: the ⌘K palette and the create dialog.
 *
 * They live at the SHELL, not in the rail, because the rail now renders twice
 * — once as the desktop aside, once inside the mobile sheet — and a second
 * mount would mean a second command palette listening for ⌘K and a second copy
 * of the create form's state.
 */
export function FleetOverlays({
  snapshot,
}: {
  snapshot: DashboardSnapshotView | undefined;
}): React.ReactNode {
  const open = useCreateWorkspaceUi((state) => state.open);
  const repoRoot = useCreateWorkspaceUi((state) => state.repoRoot);
  const setOpen = useCreateWorkspaceUi((state) => state.setOpen);
  const setRepoRoot = useCreateWorkspaceUi((state) => state.setRepoRoot);

  const rows = useMemo(() => fleetRows(snapshot), [snapshot]);
  const firstRepoRoot = snapshot?.projects[0]?.repo_root ?? "";

  return (
    <>
      <FleetPalette rows={rows} />
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
