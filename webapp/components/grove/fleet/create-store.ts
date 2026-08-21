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
 * The dialog is mounted ONCE, in the shell, because the shell is the only
 * surface present on every route. Its openers — the dashboard toolbar and the
 * launch surface's "More options" — sit in separate trees with no common
 * parent below that shell, so the intent travels through this store instead of
 * a prop chain. Mounting a second dialog per opener would mean a second copy
 * of its form state.
 *
 * The rail's `+` is NOT one of them: starting a workspace is a route (`/`, the
 * launch composer), and this dialog is the elaboration reachable from there.
 */
export const useCreateWorkspaceUi = create<CreateWorkspaceUi>((set) => ({
  open: false,
  repoRoot: "",
  openFor: (repoRoot) => set({ open: true, repoRoot }),
  setOpen: (open) => set({ open }),
  setRepoRoot: (repoRoot) => set({ repoRoot }),
}));
