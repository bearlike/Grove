"use client";

import { create } from "zustand";

interface CreateWorkspaceUi {
  readonly open: boolean;
  readonly repoRoot: string;
  openFor(repoRoot: string): void;
  setOpen(open: boolean): void;
  setRepoRoot(repoRoot: string): void;
}

/**
 * Whether the create dialog is open, and which repo it opens on.
 *
 * The dialog is mounted ONCE, in the sidebar, because the sidebar is the only
 * surface present on every route. The four things that open it — the sidebar's
 * `+`, the dashboard toolbar, each project header, the empty state — are
 * scattered across two trees with no common parent below the shell, so the
 * intent travels through this store instead of a prop chain. Mounting a second
 * dialog per opener would mean a second copy of its form state.
 */
export const useCreateWorkspaceUi = create<CreateWorkspaceUi>((set) => ({
  open: false,
  repoRoot: "",
  openFor: (repoRoot) => set({ open: true, repoRoot }),
  setOpen: (open) => set({ open }),
  setRepoRoot: (repoRoot) => set({ repoRoot }),
}));
