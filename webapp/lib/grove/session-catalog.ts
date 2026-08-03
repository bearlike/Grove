import type { SessionSummaryView } from "./types";

/**
 * Pure shaping for the host-wide Session Catalog screen — kept apart from
 * the React layer the same way `computeFacets` and `chatItemsFromTurns`
 * are, so the grouping/ordering/honesty rules are unit-testable without a DOM.
 *
 * The catalog is a metadata-only listing: a row carries where a session ran
 * (`cwd`, `project`), when (`modified_at`), which tool (`adapter_kind`), which
 * branch (`git_branch`), whether Grove manages it (`workspace_id`), and an
 * honest directory-level `live` — but NO parsed `activity`, `title`, or
 * prompts. Nothing here invents any of those.
 */

/** One project's rows on the catalog screen. */
export interface CatalogGroup {
  /** Stable React key + identity: the repo root, or `""` for the no-repo bucket. */
  key: string;
  /** Display name; null when `cwd` resolves to no git repo at all. */
  repoName: string | null;
  repoRoot: string | null;
  /** True when the enclosing checkout is a Grove-created worktree. */
  isGroveManaged: boolean;
  /** Newest-first. */
  sessions: SessionSummaryView[];
}

/** The one ordering rule, shared by rows and by groups: newest activity first.
 *  A null timestamp sinks — it is unknown, not ancient. */
function byModifiedDesc(a: SessionSummaryView, b: SessionSummaryView): number {
  return (b.modified_at ?? "").localeCompare(a.modified_at ?? "");
}

/**
 * The row's display label. A catalog row has NO parsed title or prompt (the
 * head read never opens the transcript), so identity comes from what the scan
 * genuinely recovered, in descending order of usefulness to a reader: the
 * Grove workspace's own title, else the branch the session recorded, else the
 * bare session id. Never a fabricated "untitled session" that hides which of
 * the three you are actually looking at.
 */
export function catalogRowLabel(row: SessionSummaryView): string {
  return row.workspace_title || row.git_branch || row.session_id;
}

/**
 * A session is drillable only when the scan recovered the directory it ran in:
 * the turns route resolves a workspace-less session by `(kind, cwd, id)`, so a
 * row without `cwd` (~2 % of Claude transcripts) has no coordinate to open. Such
 * rows are still LISTED — hiding them would misreport what is on the host — they
 * simply do not link.
 */
export function isDrillable(row: SessionSummaryView): boolean {
  return Boolean(row.cwd);
}

/**
 * The subdirectory a session ran in, relative to its repo root — `null` when it
 * ran at the root itself or there is no repo to relativize against. Lexical and
 * separator-aware (`/a/bc` is not under `/a/b`), never touching a filesystem,
 * mirroring the engine's own `FileEditView._relativize`.
 */
export function relativeCwd(row: SessionSummaryView): string | null {
  const { cwd, project } = row;
  if (!cwd || !project) return null;
  const root = project.repo_root.replace(/\/+$/, "");
  if (cwd === root) return null;
  return cwd.startsWith(`${root}/`) ? cwd.slice(root.length + 1) : null;
}

/** Case-insensitive substring match over the fields a catalog row actually has.
 *  Deliberately NOT the rail's `matchesQuery`: that searches parsed titles and
 *  prompts, which a catalog row never carries. */
export function matchesCatalogQuery(row: SessionSummaryView, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [
    row.workspace_title,
    row.git_branch,
    row.session_id,
    row.adapter_kind,
    row.cwd,
    row.project?.repo_name,
  ].some((v) => v != null && v.toLowerCase().includes(q));
}

/**
 * Group the catalog by project, newest-first within each group AND across
 * groups (a group is as recent as its newest session). Sessions whose `cwd`
 * resolves to no git repository collect in one bucket keyed `""` — it takes its
 * place by recency like any other group rather than being pinned or hidden,
 * because "this ran outside a repo" is a fact about the host, not an error.
 */
export function groupCatalog(
  rows: SessionSummaryView[],
  query = "",
): CatalogGroup[] {
  const groups = new Map<string, CatalogGroup>();
  for (const row of rows) {
    if (!matchesCatalogQuery(row, query)) continue;
    const key = row.project?.repo_root ?? "";
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        repoName: row.project?.repo_name ?? null,
        repoRoot: row.project?.repo_root ?? null,
        isGroveManaged: false,
        sessions: [],
      };
      groups.set(key, group);
    }
    // Any Grove-managed checkout in the group marks it — one row is enough to
    // say "Grove tends this repo", and the per-row provenance still shows which.
    if (row.project?.is_grove_managed) group.isGroveManaged = true;
    group.sessions.push(row);
  }
  for (const group of groups.values()) group.sessions.sort(byModifiedDesc);
  return [...groups.values()].sort((a, b) =>
    byModifiedDesc(a.sessions[0], b.sessions[0]),
  );
}
