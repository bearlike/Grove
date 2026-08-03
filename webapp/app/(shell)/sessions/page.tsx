"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { SessionCatalogList } from "@/components/sessions/session-catalog-list";
import { useSessionCatalog } from "@/lib/grove/hooks";

/** The daemon bounds `limit` at 200; ask for all of it — this screen exists to
 *  be the wide view, and the rows are metadata-only, so the cost is one request
 *  and zero transcript parses either way. */
const CATALOG_LIMIT = 200;

/**
 * `/sessions` — the host-wide Session Catalog, the only place a session with
 * no Grove workspace is reachable at all.
 *
 * **Why this is not the rail at a different width.** The rail lists sessions per
 * KNOWN project (`useProjectSessionsAll` fans `GET /sessions?repo=` across
 * `cfg.projects`), full-parsed and live-overlaid, and every row navigates into a
 * live workspace. It structurally cannot show a session in a repo Grove has
 * never heard of, because it only ever iterates repos Grove knows. This screen
 * reads the host scope instead — ONE `GET /sessions` with no `repo`, covering
 * every adapter's whole store — and its rows open a read-only archive. Two
 * scopes, two jobs; the rail is deliberately left exactly as it was.
 *
 * Search is page-local `useState`, NOT the shared `ui-store.query`: that field
 * is documented as the rail-and-grid filter, and a third consumer at a different
 * scope would mean typing in the rail silently narrows a full-page catalog.
 *
 * Read-only by construction — this screen has no mutation of any kind, matching
 * the webapp's read-first contract and the fact that most catalog rows have no
 * workspace to act on.
 *
 * Test seams: `sessions-page`, `sessions-search`, `sessions-count`,
 * `sessions-limit-note`, plus the `session-catalog*` seams the list owns.
 */
export default function SessionsPage() {
  const [query, setQuery] = useState("");
  const { data, isLoading, isError, error } = useSessionCatalog(CATALOG_LIMIT);
  const rows = data ?? [];
  // The daemon slices AFTER its newest-first sort, so a full page means "these
  // are the most recent N", not "this is everything" — say so rather than let
  // the screen imply the host has exactly 200 sessions.
  const truncated = rows.length >= CATALOG_LIMIT;

  return (
    <div
      data-testid="sessions-page"
      className="mx-auto flex w-full max-w-[72rem] flex-1 flex-col gap-4 p-4 pb-[env(safe-area-inset-bottom)]"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <h1 className="text-base font-semibold">Sessions</h1>
          <p className="text-xs text-muted-foreground">
            Every agent session recorded on this host, grouped by project.
          </p>
        </div>
        <div className="relative w-full min-w-0 sm:w-64">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
          />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by project, branch, agent…"
            aria-label="Filter sessions"
            data-testid="sessions-search"
            className="h-8 pl-8 text-[13px] placeholder:text-muted-foreground/70"
          />
        </div>
      </div>

      {isError && (
        <div
          role="alert"
          className="rounded-md border border-[var(--status-error)] bg-[var(--status-error)]/10 p-4 text-sm"
        >
          Could not load the session catalog: {(error as Error).message}
        </div>
      )}

      <SessionCatalogList rows={rows} query={query} isLoading={isLoading} />

      {rows.length > 0 && (
        <p
          data-testid="sessions-count"
          className="px-1 pt-2 text-[11px] text-muted-foreground"
        >
          {truncated ? (
            <span data-testid="sessions-limit-note">
              Showing the {CATALOG_LIMIT} most recently active sessions.
            </span>
          ) : (
            `${rows.length} session${rows.length === 1 ? "" : "s"}.`
          )}
        </p>
      )}
    </div>
  );
}
