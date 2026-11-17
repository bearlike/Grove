"use client";

import { create } from "zustand";

interface CreateWorkspaceUi {
  readonly open: boolean;
  setOpen(open: boolean): void;
}

/**
 * Whether More options is open.
 *
 * Its openers (the fleet toolbar, the fleet empty state and the landing
 * surface) sit in trees with no common parent below the shell, so the intent
 * travels through this store. It carries no project: the dialog edits the one
 * shell-level draft (`LaunchDraftProvider`), whose project pill already
 * resolves the remembered or first project, so an opener has nothing to add.
 */
export const useCreateWorkspaceUi = create<CreateWorkspaceUi>((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));
