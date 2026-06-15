"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { AgentActivityState, BranchPlan } from "./types";

/**
 * The ONE client-state store (issue #96). Zustand owns every piece of
 * cross-component *UI* state — the composer draft, the sidebar collapse, the
 * view scope/filter, and the single live-pane focus. SERVER state stays in
 * TanStack Query (`hooks.ts`); the two layers never mix (a TanStack cache is a
 * read-model of the daemon, this store is local intent the daemon never sees).
 *
 * Why Zustand over context+reducer: selector subscriptions mean a composer
 * keystroke never re-renders the grid, with zero provider boilerplate; one
 * tiny dep replaced three ad-hoc state homes (`use-sidebar-state.ts`,
 * `filter-persistence.ts`, and a page-local `useState` web) — a net deletion.
 *
 * Persistence (the durable slices only — `sidebarCollapsed`, `hiddenStates`,
 * `attentionOnly`) rides the `persist` middleware with `skipHydration`: the
 * store boots at defaults on the server AND the first client paint (so the
 * markup matches), then `rehydrate()` (called once on mount in providers)
 * flips to the stored values. `hydrated` lets a component hold the default
 * shape until then, the same mount-guard the old `useSidebarState` used.
 */

/** The five branch modes — mirrors the wire `BranchPlan` discriminant. */
export type BranchMode = BranchPlan["kind"];

export interface ComposerDraft {
  /** The prompt textarea — the create surface; the first line becomes the title. */
  prompt: string;
  /** Selected agent name; null until the agent list resolves and defaults it. */
  agentName: string | null;
  /** null = the adapter's default model; else an explicit (incl. custom) model id. */
  model: string | null;
  /** Create-target repo root; null until the project list resolves and defaults it. */
  repoRoot: string | null;
  branchMode: BranchMode;
  baseRef: string;
  newName: string;
  existingName: string;
  remoteRef: string;
  remoteLocal: string;
  skipInit: boolean;
  /** Whether the Advanced ▾ disclosure (branch source + skip-init) is open. */
  advancedOpen: boolean;
}

const INITIAL_DRAFT: ComposerDraft = {
  prompt: "",
  agentName: null,
  model: null,
  repoRoot: null,
  branchMode: "auto",
  baseRef: "HEAD",
  newName: "",
  existingName: "",
  remoteRef: "",
  remoteLocal: "",
  skipInit: false,
  advancedOpen: false,
};

export interface UiStore {
  /** True once the persisted slices have been read back on the client. */
  hydrated: boolean;

  // ─── Composer draft (deliverable A) ────────────────────────────────────────
  composer: ComposerDraft;
  patchComposer: (patch: Partial<ComposerDraft>) => void;
  /** After a successful create: clear the prompt + advanced fields, keep the
   *  agent/model/repo picks so the next create starts where this one left off. */
  resetComposerAfterCreate: () => void;

  // ─── Sidebar (deliverable B; replaces use-sidebar-state.ts) ─────────────────
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;

  // ─── View scope + filter (deliverable B; replaces filter-persistence.ts) ────
  /** Free-text search over title/branch — transient, never persisted. */
  query: string;
  setQuery: (q: string) => void;
  /** Repo scope: null = all repos, else a `repo_root` the grid narrows to. */
  scopeRepo: string | null;
  setScopeRepo: (r: string | null) => void;
  /** Agent-states to HIDE (empty = show all; a later state is visible by default). */
  hiddenStates: AgentActivityState[];
  toggleState: (s: AgentActivityState) => void;
  attentionOnly: boolean;
  setAttentionOnly: (v: boolean) => void;
  clearFilters: () => void;

  // ─── Single live-pane focus (replaces page-local useState) ──────────────────
  liveId: string | null;
  toggleLive: (id: string) => void;
  clearLive: () => void;
}

export const useUiStore = create<UiStore>()(
  persist(
    (set) => ({
      hydrated: false,

      composer: INITIAL_DRAFT,
      patchComposer: (patch) =>
        set((s) => ({ composer: { ...s.composer, ...patch } })),
      resetComposerAfterCreate: () =>
        set((s) => ({
          composer: {
            ...INITIAL_DRAFT,
            agentName: s.composer.agentName,
            model: s.composer.model,
            repoRoot: s.composer.repoRoot,
          },
        })),

      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

      query: "",
      setQuery: (q) => set({ query: q }),
      scopeRepo: null,
      setScopeRepo: (r) => set({ scopeRepo: r }),
      hiddenStates: [],
      toggleState: (state) =>
        set((s) => ({
          hiddenStates: s.hiddenStates.includes(state)
            ? s.hiddenStates.filter((x) => x !== state)
            : [...s.hiddenStates, state],
        })),
      attentionOnly: false,
      setAttentionOnly: (v) => set({ attentionOnly: v }),
      clearFilters: () =>
        set({ scopeRepo: null, hiddenStates: [], attentionOnly: false, query: "" }),

      liveId: null,
      toggleLive: (id) => set((s) => ({ liveId: s.liveId === id ? null : id })),
      clearLive: () => set({ liveId: null }),
    }),
    {
      name: "grove:ui",
      storage: createJSONStorage(() => localStorage),
      // Only the durable view/chrome preferences persist; the composer draft and
      // transient focus/search are intentionally session-local.
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        hiddenStates: s.hiddenStates,
        attentionOnly: s.attentionOnly,
      }),
      skipHydration: true,
      onRehydrateStorage: () => (state) => {
        if (state) state.hydrated = true;
      },
    },
  ),
);
