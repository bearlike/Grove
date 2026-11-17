/**
 * The add-project wizard's decisions, pure so they are testable without a dialog.
 *
 * Validation here MIRRORS the daemon's (`contracts/projects.py`) so a refusal
 * lands beside the field instead of as an opaque 422 — the daemon still owns
 * the rule and still refuses, this only says so earlier. The selection rollup
 * keys on the canonical path because two folders can share a name.
 */

import type {
  AdoptProject,
  CloneProject,
  CreateProject,
  ProjectCandidateView,
} from "@/lib/grove/api";

export type AddProjectMethod = "create" | "clone" | "adopt";

/** One path segment, never flag-shaped: the daemon's `ProjectName` pattern. */
const NAME = /^[A-Za-z0-9_][A-Za-z0-9._-]*$/;

/** Why `name` would be refused, or null when the daemon would accept it. */
export function projectNameError(name: string): string | null {
  if (name.length === 0) return "Enter a name.";
  if (name.length > 100) return "Use 100 characters or fewer.";
  if (name.includes("/") || name.includes("\\")) return "Use one folder name, not a path.";
  if (!NAME.test(name)) {
    return "Start with a letter, digit or underscore; then letters, digits, '.', '_' or '-'.";
  }
  return null;
}

/** Why `url` would be refused before git ever sees it, or null. */
export function cloneUrlError(url: string): string | null {
  const trimmed = url.trim();
  if (trimmed.length === 0) return "Enter a repository URL.";
  if (/\s/.test(trimmed)) return "A URL cannot contain spaces.";
  if (trimmed.startsWith("-")) return "A URL cannot start with '-'.";
  return null;
}

/** Why a typed absolute path would be refused, or null. */
export function absolutePathError(path: string): string | null {
  const trimmed = path.trim();
  if (trimmed.length === 0) return null;
  if (!trimmed.startsWith("/") && !trimmed.startsWith("~")) {
    return "Use an absolute path, or one starting with ~.";
  }
  return null;
}

/**
 * The folder `git clone` itself would choose — and the daemon's
 * `name_from_url` — so the preview never disagrees with the result.
 */
export function folderFromUrl(url: string): string {
  const segments = url.trim().replace(/\/+$/, "").split(/[/:]/).filter(Boolean);
  if (segments.length > 1 && segments[segments.length - 1] === ".git") segments.pop();
  const tail = segments[segments.length - 1] ?? "";
  const name = tail.endsWith(".git") ? tail.slice(0, -4) : tail;
  return NAME.test(name) ? name : "";
}

/** Location + folder, joined for display. `null` while either half is missing. */
export function destinationPreview(location: string, name: string): string | null {
  const parent = location.trim().replace(/\/+$/, "");
  if (parent.length === 0 || name.length === 0) return null;
  return `${parent}/${name}`;
}

/** A location the user did not change is omitted, so the daemon resolves its own default. */
function locationField(location: string, defaultRoot: string): { location?: string } {
  const trimmed = location.trim();
  return trimmed.length === 0 || trimmed === defaultRoot ? {} : { location: trimmed };
}

export function createRequest(name: string, location: string, defaultRoot: string): CreateProject {
  return { kind: "create", name, ...locationField(location, defaultRoot) };
}

export function cloneRequest(
  url: string,
  name: string,
  location: string,
  defaultRoot: string,
): CloneProject {
  const derived = folderFromUrl(url);
  return {
    kind: "clone",
    url: url.trim(),
    // Sending the derived name back would be harmless; omitting it keeps the
    // daemon the one place that decides what an untouched field means.
    ...(name.length > 0 && name !== derived ? { name } : {}),
    ...locationField(location, defaultRoot),
  };
}

export function adoptRequests(paths: readonly string[]): AdoptProject[] {
  return paths.map((path) => ({ kind: "adopt", path }));
}

/** Case-insensitive match on name, path or remote — the three columns a reader sees. */
export function filterCandidates(
  candidates: readonly ProjectCandidateView[],
  query: string,
): ProjectCandidateView[] {
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) return [...candidates];
  return candidates.filter((c) =>
    [c.name, c.path, c.remote ?? ""].some((field) => field.toLowerCase().includes(needle)),
  );
}

export interface SelectionSummary {
  /** Rows the filter keeps — every match, not just what is scrolled into view. */
  readonly matching: number;
  /** Unique selected paths that still exist among the candidates. */
  readonly selected: number;
  /** Selected paths the current filter hides. */
  readonly hidden: number;
}

/**
 * The counts the wizard prints. Selection is by canonical path, so a filter
 * that hides a chosen row does not unselect it — it moves it into `hidden`.
 * A selected path no longer among the candidates (added elsewhere meanwhile)
 * is dropped rather than counted, so "Add N" never names a phantom.
 */
export function selectionSummary(
  candidates: readonly ProjectCandidateView[],
  visible: readonly ProjectCandidateView[],
  selected: ReadonlySet<string>,
): SelectionSummary {
  const known = new Set(candidates.map((c) => c.path));
  const shown = new Set(visible.map((c) => c.path));
  let live = 0;
  let hidden = 0;
  for (const path of selected) {
    if (!known.has(path)) continue;
    live += 1;
    if (!shown.has(path)) hidden += 1;
  }
  return { matching: visible.length, selected: live, hidden };
}

/** The primary action's label: the effect and its count, never "Continue". */
export function adoptActionLabel(count: number): string {
  return count === 1 ? "Add 1 project" : `Add ${count} projects`;
}

/** The selected paths still among the candidates, in candidate order. */
export function selectedPaths(
  candidates: readonly ProjectCandidateView[],
  selected: ReadonlySet<string>,
): string[] {
  return candidates.filter((c) => selected.has(c.path)).map((c) => c.path);
}
