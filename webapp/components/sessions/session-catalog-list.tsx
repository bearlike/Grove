"use client";

import Link from "next/link";
import { FolderGit2, GitBranch } from "lucide-react";
import { MetaRow } from "@/components/shared/meta";
import { relativeTimeLabel } from "@/components/shared/relative-time";
import { Skeleton } from "@/components/ui/skeleton";
import {
  catalogRowLabel,
  groupCatalog,
  isDrillable,
  relativeCwd,
  type CatalogGroup,
} from "@/lib/grove/session-catalog";
import type { SessionSummaryView } from "@/lib/grove/types";
import { cn } from "@/lib/utils";

/**
 * The host-wide Session Catalog, grouped by project — every agent
 * session on this host, including ones in repos Grove has never managed.
 *
 * Renders from CATALOG METADATA ONLY: one `GET /sessions` served the whole
 * screen, and no transcript is read until a row is opened. That is why a row
 * shows no agent state and no prompt text — the host scan pays one bounded head
 * read per session and deliberately never parses. Showing an `idle` mark or an
 * "untitled session" here would be inventing measurements the wire says it did
 * not take (see `SessionSummaryView`'s null contract).
 *
 * Two honest-degradation cases are rendered, never hidden:
 *   · **no git repository** — `project` is null, so the row joins the one
 *     trailing-keyed group headed "No git repository".
 *   · **unknown location** — `cwd` is null, so there is no `(kind, cwd, id)`
 *     coordinate to open the transcript by. The row still LISTS (it exists on
 *     the host) but is inert and dimmed, and says why in its meta line.
 *
 * Quiet-chrome holds: the only hue is the branch teal (`--ref-branch`, the app's
 * branch token) and the live dot's `--status-active`. Live is never signalled by
 * color alone — the dot carries an sr-only "live" label and a tooltip.
 *
 * Test seams: `session-catalog`, `session-catalog-group` (+ `data-repo-root`),
 * `session-catalog-group-name`, `session-catalog-row` (+ `data-session-id`,
 * `data-drillable`, `data-live`, `data-grove`), `session-catalog-live`,
 * `session-catalog-branch`, `session-catalog-empty`.
 */
export function SessionCatalogList({
  rows,
  query,
  isLoading,
}: {
  rows: SessionSummaryView[];
  /** Free-text narrowing over the fields a catalog row actually carries. */
  query: string;
  isLoading: boolean;
}) {
  const groups = groupCatalog(rows, query);

  if (isLoading && rows.length === 0) {
    return (
      <div data-testid="session-catalog" className="flex flex-col gap-2">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    );
  }

  if (groups.length === 0) {
    return (
      <p
        data-testid="session-catalog-empty"
        className="px-1 py-10 text-center text-sm text-muted-foreground"
      >
        {/* "Nothing here" and "your search hid it" are different facts; a
            filtered-empty screen must never read as an empty host. */}
        {rows.length > 0
          ? "No sessions match your search."
          : "No agent sessions found on this host."}
      </p>
    );
  }

  return (
    <div data-testid="session-catalog" className="flex flex-col gap-6">
      {groups.map((group) => (
        <CatalogGroupSection key={group.key} group={group} />
      ))}
    </div>
  );
}

function CatalogGroupSection({ group }: { group: CatalogGroup }) {
  return (
    <section
      data-testid="session-catalog-group"
      data-repo-root={group.repoRoot ?? ""}
      className="flex flex-col gap-1"
    >
      {/* Section label tier (11px semibold uppercase) — the panel-header cue,
          separated from its rows by space rather than a drawn line. */}
      <div className="flex items-baseline gap-2 px-2.5 pb-1">
        <FolderGit2 aria-hidden className="size-3.5 shrink-0 text-muted-foreground" />
        <h2
          data-testid="session-catalog-group-name"
          title={group.repoRoot ?? undefined}
          className="min-w-0 truncate text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground"
        >
          {group.repoName ?? "No git repository"}
        </h2>
        <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">
          {group.sessions.length}
        </span>
      </div>
      <ul className="flex flex-col gap-0.5">
        {group.sessions.map((row) => (
          <CatalogRow key={`${row.adapter_kind}:${row.session_id}`} row={row} />
        ))}
      </ul>
    </section>
  );
}

/**
 * The live cue: a same-directory agent process is running AND this transcript is
 * fresh. Deliberately NOT an agent-state mark — the catalog never parses a
 * transcript, so it cannot know working/waiting/idle, only that the directory is
 * busy. Color is an enhancement; the sr-only word carries the signal alone.
 */
function LiveDot() {
  return (
    <span
      data-testid="session-catalog-live"
      title="An agent is running in this directory"
      className="inline-flex shrink-0 items-center"
    >
      <span
        aria-hidden
        className="size-1.5 rounded-full bg-[var(--status-active)] animate-grove-pulse motion-reduce:animate-none"
      />
      <span className="sr-only">live</span>
    </span>
  );
}

function CatalogRow({ row }: { row: SessionSummaryView }) {
  const label = catalogRowLabel(row);
  const drillable = isDrillable(row);
  const subdir = relativeCwd(row);
  const when = row.modified_at ? relativeTimeLabel(row.modified_at) : null;
  // The branch already leads the row when it IS the label; repeating it in the
  // meta line would spend a slot on a duplicate.
  const branch = row.git_branch && row.git_branch !== label ? row.git_branch : null;

  const head = (
    <span className="flex items-center gap-2">
      {row.live && <LiveDot />}
      <span className="min-w-0 flex-1 truncate text-sm leading-5">{label}</span>
      {when && (
        <span
          title={row.modified_at ?? undefined}
          className="shrink-0 text-[11px] leading-5 tabular-nums text-muted-foreground"
        >
          {when}
        </span>
      )}
    </span>
  );

  const meta = (
    <MetaRow className="flex-nowrap overflow-hidden text-[11px] leading-4">
      <span className="shrink-0 font-mono">{row.adapter_kind}</span>
      {branch && (
        <span
          data-testid="session-catalog-branch"
          title={branch}
          className="inline-flex min-w-0 items-center gap-1"
        >
          <GitBranch
            aria-hidden
            className="size-3 shrink-0"
            style={{ color: "var(--ref-branch)" }}
          />
          <span className="min-w-0 truncate font-mono">{branch}</span>
        </span>
      )}
      {subdir && <span className="min-w-0 truncate font-mono">{subdir}</span>}
      {/* Provenance: Grove launched or adopted this one and still tracks a
          workspace for it — the fact that makes a row actionable elsewhere. */}
      {row.workspace_id !== null && <span className="shrink-0 font-medium">grove</span>}
      {!drillable && <span className="shrink-0">location unknown</span>}
    </MetaRow>
  );

  const rowAttrs = {
    "data-testid": "session-catalog-row",
    "data-session-id": row.session_id,
    "data-drillable": String(drillable),
    "data-live": String(row.live),
    "data-grove": String(row.workspace_id !== null),
  };

  if (!drillable) {
    // No recorded cwd ⇒ no coordinate the turns route can resolve. Inert and
    // dimmed, with the reason in the tooltip — listed, never hidden.
    return (
      <li
        {...rowAttrs}
        title="Location unknown — this transcript records no working directory, so its conversation cannot be opened."
        className="flex flex-col gap-0.5 rounded-md px-2.5 py-1.5 opacity-55"
      >
        {head}
        {meta}
      </li>
    );
  }

  return (
    <li {...rowAttrs} className={cn("flex rounded-md transition-colors hover:bg-muted")}>
      <Link
        href={{
          pathname: `/sessions/${encodeURIComponent(row.session_id)}`,
          // `cwd` round-trips byte-for-byte: the adapters match a RECORDED cwd
          // by string, so the drill-in must pass back exactly what the row
          // reported, never a normalized or re-joined path.
          query: { kind: row.adapter_kind, cwd: row.cwd as string },
        }}
        aria-label={`Open transcript — ${label}`}
        className="flex min-w-0 flex-1 flex-col gap-0.5 rounded-md px-2.5 py-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        {head}
        {meta}
      </Link>
    </li>
  );
}
