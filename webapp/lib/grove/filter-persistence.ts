import type { AgentActivityState } from "./types";
import { emptyFilter, type DashboardFilterState } from "./dashboard-filter";

/**
 * Persist the consolidated dashboard filter across reloads and the refresh-now
 * action, so a user who narrowed the wall doesn't lose it on the next visit.
 *
 * `DashboardFilterState` carries `Set`s (hidden projects / states) which don't
 * survive `JSON.stringify`, so we serialize Sets ↔ arrays at the boundary. All
 * access is `typeof window`-guarded (SSR / jsdom safe) and best-effort: a
 * corrupt or unavailable `localStorage` degrades to `emptyFilter()` — "show
 * everything" — never a thrown error mid-render.
 */
const STORAGE_KEY = "grove.dashboard.filter";

interface PersistedFilter {
  hiddenProjects: string[];
  hiddenStates: AgentActivityState[];
  attentionOnly: boolean;
}

export function loadFilter(): DashboardFilterState {
  if (typeof window === "undefined") return emptyFilter();
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return emptyFilter(); // storage disabled (private mode / blocked)
  }
  if (!raw) return emptyFilter();
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedFilter>;
    return {
      hiddenProjects: new Set(Array.isArray(parsed.hiddenProjects) ? parsed.hiddenProjects : []),
      hiddenStates: new Set(Array.isArray(parsed.hiddenStates) ? parsed.hiddenStates : []),
      attentionOnly: parsed.attentionOnly === true,
    };
  } catch {
    return emptyFilter(); // malformed JSON
  }
}

export function saveFilter(filter: DashboardFilterState): void {
  if (typeof window === "undefined") return;
  const payload: PersistedFilter = {
    hiddenProjects: [...filter.hiddenProjects],
    hiddenStates: [...filter.hiddenStates],
    attentionOnly: filter.attentionOnly,
  };
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // storage full / disabled — persistence is best-effort, never fatal
  }
}
