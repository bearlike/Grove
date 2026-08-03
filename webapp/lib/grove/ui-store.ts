"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { AgentActivityState, BranchPlan, Runtime } from "./types";

/**
 * The ONE client-state store. Zustand owns every piece of
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
 * Persistence (the durable CHROME + rail-filter prefs — `sidebarCollapsed`,
 * `landingView`, and the rail filters `hiddenStates`/`hiddenProjects`/
 * `attentionOnly`/`showUnmapped`) rides the `persist` middleware with
 * `skipHydration`: the store boots at defaults on the server AND the first
 * client paint (so the markup matches), then `rehydrate()` (called once on mount
 * in providers) flips to the stored values. `hydrated` lets a component hold the
 * default shape until then, the same mount-guard the old `useSidebarState` used.
 * The rail filters are safe to persist because the rail's compact filter
 * (`SidebarFilter`) is the always-reachable clear-path; only `query` and
 * `scopeRepo` stay transient — a stale search/scope would silently empty the
 * rail with no obvious way to clear it, so those reset to "show all" each load.
 */

/** The two landing views (design §4.1): the composer-hero vs the card grid. */
export type LandingView = "hero" | "overview";

/** The five branch modes — mirrors the wire `BranchPlan` discriminant. */
export type BranchMode = BranchPlan["kind"];

export interface ComposerDraft {
  /** The prompt textarea — the create surface; the first line becomes the title. */
  prompt: string;
  /** Selected agent name; null until the agent list resolves and defaults it. */
  agentName: string | null;
  /** null = the adapter's default model; else an explicit (incl. custom) model id. */
  model: string | null;
  /** null = cascade to `container.enabled`'s config default; else an explicit,
   *  create-time-only choice — mirrors `model`'s null-is-default wire
   *  convention. Never editable after create on any surface. */
  runtime: Runtime | null;
  /** null = cascade to `brief.enabled`'s config default; else an explicit,
   *  create-time-only choice — the same null-is-default wire convention as
   *  `runtime`/`model`. Controls whether the agent gets Grove's one-time
   *  first-turn brief pointing it at the `working-in-grove` skill. */
  brief: boolean | null;
  /** Create-target repo root; null until the project list resolves and defaults it. */
  repoRoot: string | null;
  branchMode: BranchMode;
  baseRef: string;
  newName: string;
  existingName: string;
  remoteRef: string;
  remoteLocal: string;
  skipInit: boolean;
  /** Adopt an existing agent session instead of minting a fresh one — rides
   *  as `CreateWorkspaceRequest.resume_session_id`. Empty = mint fresh
   *  (the default); a full session id resumes it (claude_code/codex only, the
   *  engine 422s otherwise). Create-only, like `skipInit`. */
  resumeSessionId: string;
  /** Whether the Advanced ▾ disclosure (branch source + skip-init) is open. */
  advancedOpen: boolean;
}

const INITIAL_DRAFT: ComposerDraft = {
  prompt: "",
  agentName: null,
  model: null,
  runtime: null,
  brief: null,
  repoRoot: null,
  branchMode: "auto",
  baseRef: "HEAD",
  newName: "",
  existingName: "",
  remoteRef: "",
  remoteLocal: "",
  skipInit: false,
  resumeSessionId: "",
  advancedOpen: false,
};

export interface UiStore {
  /** True once the persisted slices have been read back on the client. */
  hydrated: boolean;

  // ─── Composer draft ─────────────────────────────────────────────────────────
  composer: ComposerDraft;
  patchComposer: (patch: Partial<ComposerDraft>) => void;
  /** After a successful create: clear the prompt + advanced fields, keep the
   *  agent/model/repo picks so the next create starts where this one left off. */
  resetComposerAfterCreate: () => void;

  // ─── Sidebar ─────────────────────────────────────────────────────────────
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;

  // ─── Landing view (persisted per-user, design §4.1) ─────────────────────────
  /** `hero` = composer-hero (default, clean first-run); `overview` = card grid. */
  landingView: LandingView;
  setLandingView: (v: LandingView) => void;

  // ─── View scope + filter ─────────────────────────────────────────────────────
  /** Free-text search over title/branch — transient, never persisted. */
  query: string;
  setQuery: (q: string) => void;
  /** Repo scope: null = all repos, else a `repo_root` the grid narrows to. */
  scopeRepo: string | null;
  setScopeRepo: (r: string | null) => void;
  /** Agent-states to HIDE (empty = show all; a later state is visible by default). */
  hiddenStates: AgentActivityState[];
  toggleState: (s: AgentActivityState) => void;
  /** `repo_root`s to HIDE from the rail (empty = all projects show; a project that
   *  appears later is visible by default — the same hidden-set philosophy as
   *  `hiddenStates`, so the rail never silently drops a fresh project). */
  hiddenProjects: string[];
  toggleProject: (repoRoot: string) => void;
  attentionOnly: boolean;
  setAttentionOnly: (v: boolean) => void;
  /** Show the unmapped/metadata-only rows (default false — actionable rows only).
   *  The debugging escape hatch: unmapped sessions have no live workspace to open,
   *  so they're hidden by default and revealed on demand (the rail's hidden-note
   *  or the filter menu). */
  showUnmapped: boolean;
  setShowUnmapped: (v: boolean) => void;
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
            runtime: s.composer.runtime,
            brief: s.composer.brief,
            repoRoot: s.composer.repoRoot,
          },
        })),

      sidebarCollapsed: false,
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),

      landingView: "hero",
      setLandingView: (v) => set({ landingView: v }),

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
      hiddenProjects: [],
      toggleProject: (repoRoot) =>
        set((s) => ({
          hiddenProjects: s.hiddenProjects.includes(repoRoot)
            ? s.hiddenProjects.filter((x) => x !== repoRoot)
            : [...s.hiddenProjects, repoRoot],
        })),
      attentionOnly: false,
      setAttentionOnly: (v) => set({ attentionOnly: v }),
      showUnmapped: false,
      setShowUnmapped: (v) => set({ showUnmapped: v }),
      clearFilters: () =>
        set({
          scopeRepo: null,
          hiddenStates: [],
          hiddenProjects: [],
          attentionOnly: false,
          showUnmapped: false,
          query: "",
        }),

      liveId: null,
      toggleLive: (id) => set((s) => ({ liveId: s.liveId === id ? null : id })),
      clearLive: () => set({ liveId: null }),
    }),
    {
      name: "grove:ui",
      storage: createJSONStorage(() => localStorage),
      // Durable CHROME + rail-filter preferences persist; the composer draft and
      // transient search stay session-local. `hiddenStates`/`attentionOnly` are
      // safe to persist because the rail's compact filter (SidebarFilter) is
      // the always-reachable clear-path (`scopeRepo`/`query` stay transient: a
      // stale scope/search would silently empty the rail with no way to clear it).
      partialize: (s) => ({
        sidebarCollapsed: s.sidebarCollapsed,
        landingView: s.landingView,
        hiddenStates: s.hiddenStates,
        hiddenProjects: s.hiddenProjects,
        attentionOnly: s.attentionOnly,
        showUnmapped: s.showUnmapped,
      }),
      skipHydration: true,
      onRehydrateStorage: () => (state) => {
        if (state) state.hydrated = true;
      },
    },
  ),
);
