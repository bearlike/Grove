"use client";

import { create } from "zustand";

/**
 * Where the add-project wizard was opened from, and what to do with the result.
 *
 * A store for `useAnnotationUi`'s reason: the dialog is mounted ONCE in the
 * shell, and its three openers (the rail's project menu, the launch composer's
 * project picker, the no-projects empty state) sit in trees with no common
 * parent below it. `onAdded` lets an opener select what was added without the
 * dialog knowing who asked.
 */
export interface AddProjectRequestContext {
  /** Called once per registered project, with its `repo_root` and `cwd`. */
  readonly onAdded?: (project: { readonly repoRoot: string; readonly cwd: string }) => void;
}

interface AddProjectUi {
  readonly open: boolean;
  readonly context: AddProjectRequestContext;
  openWizard(context?: AddProjectRequestContext): void;
  close(): void;
}

export const useAddProjectUi = create<AddProjectUi>((set) => ({
  open: false,
  context: {},
  openWizard: (context = {}) => set({ open: true, context }),
  close: () => set({ open: false, context: {} }),
}));
