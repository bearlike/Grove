"use client";

import { Suspense, use } from "react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, GitBranch } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { MetaRow } from "@/components/shared/meta";
import { RelativeTime } from "@/components/shared/relative-time";
import { catalogRowLabel, relativeCwd } from "@/lib/grove/session-catalog";
import { useCatalogTurns } from "@/lib/grove/hooks";
import type { SessionSummaryView } from "@/lib/grove/types";

// Streamdown is a ~460 kB async chunk reached only through the transcript —
// keep the dynamic boundary on the leaf, exactly as `AgentWorkspace` does.
const ReadOnlyTranscript = dynamic(
  () => import("@/components/chat/read-only-transcript").then((m) => m.ReadOnlyTranscript),
  { ssr: false, loading: () => <Skeleton className="h-full min-h-[20rem] w-full" /> },
);

/**
 * `/sessions/[id]?kind=&cwd=` — one catalog session's conversation, READ-ONLY.
 *
 * The identity is a TRIPLE, not an id: most sessions on a host were never
 * launched by Grove, so there is no workspace to resolve through, and the daemon
 * resolves them by `(kind, cwd, session_id)` instead — which is also exactly
 * what an adapter needs to read a transcript. `cwd` therefore rides the query
 * string verbatim, byte-for-byte as the listing row reported it: the adapters
 * match a RECORDED cwd by string, so any normalization here would 404.
 *
 * Read-only is the whole contract, expressed structurally: this page renders no
 * composer, no interrupt, no lifecycle verbs, and no session-identity popover.
 * There is generally nothing running to steer — and where there IS a live Grove
 * workspace, `/w/[id]` is the surface that owns steering it.
 *
 * Its own back link (rather than the shell header's `back` arrow) because the
 * destination differs: back goes to `/sessions`, not to `/`.
 *
 * Test seams: `session-detail`, `session-detail-back`, `session-detail-title`,
 * `session-detail-error`.
 */
export default function CatalogSessionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    // `useSearchParams` needs a Suspense boundary for the route's static
    // prerender to succeed — the same pattern the rail and the login page use.
    <Suspense fallback={<DetailSkeleton />}>
      <CatalogSessionView sessionId={id} />
    </Suspense>
  );
}

function CatalogSessionView({ sessionId }: { sessionId: string }) {
  const searchParams = useSearchParams();
  const kind = searchParams.get("kind");
  const cwd = searchParams.get("cwd");
  const { data, isLoading, isError, error } = useCatalogTurns(sessionId, kind, cwd);

  return (
    <div
      data-testid="session-detail"
      className="flex h-[calc(100dvh-3.25rem)] min-h-0 min-w-0 flex-col"
    >
      <div className="flex items-center gap-2 px-3 py-2 sm:px-4">
        <Button asChild variant="ghost" size="icon-sm" aria-label="Back to sessions">
          <Link href="/sessions" data-testid="session-detail-back">
            <ArrowLeft />
          </Link>
        </Button>
        <SessionHeading session={data?.session ?? null} fallbackId={sessionId} />
      </div>

      {/* A coordinate we cannot form is a distinct failure from one the daemon
          rejected — say which, so the reader knows whether to go back or retry. */}
      {(!kind || !cwd) && (
        <p
          data-testid="session-detail-error"
          role="alert"
          className="mx-3 rounded-md bg-muted/40 px-3 py-2 text-sm text-muted-foreground sm:mx-4"
        >
          This link is missing the session&apos;s location, so its transcript cannot be
          resolved. Open it again from the sessions list.
        </p>
      )}

      {isError && (
        <p
          data-testid="session-detail-error"
          role="alert"
          className="mx-3 rounded-md bg-muted/40 px-3 py-2 text-sm text-muted-foreground sm:mx-4"
        >
          Could not read this transcript: {(error as Error).message}
        </p>
      )}

      {isLoading && kind && cwd && <DetailSkeleton />}

      {data && <ReadOnlyTranscript turns={data.turns} />}
    </div>
  );
}

/** The session's identity line. Reads only what the drill-in response carries;
 *  a catalog session has no parsed title, so the label falls back through
 *  workspace title → branch → id, the same rule the listing row uses. */
function SessionHeading({
  session,
  fallbackId,
}: {
  session: SessionSummaryView | null;
  fallbackId: string;
}) {
  const label = session ? catalogRowLabel(session) : fallbackId;
  const subdir = session ? relativeCwd(session) : null;
  const branch = session?.git_branch && session.git_branch !== label ? session.git_branch : null;

  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <h1 data-testid="session-detail-title" className="truncate text-sm font-semibold">
        {label}
      </h1>
      <MetaRow className="flex-nowrap text-[11px] leading-4">
        {session?.project?.repo_name && (
          <span className="shrink-0 truncate">{session.project.repo_name}</span>
        )}
        {session && <span className="shrink-0 font-mono">{session.adapter_kind}</span>}
        {branch && (
          <span className="inline-flex min-w-0 items-center gap-1">
            <GitBranch
              aria-hidden
              className="size-3 shrink-0"
              style={{ color: "var(--ref-branch)" }}
            />
            <span className="min-w-0 truncate font-mono">{branch}</span>
          </span>
        )}
        {subdir && <span className="min-w-0 truncate font-mono">{subdir}</span>}
        {session && <RelativeTime iso={session.modified_at} />}
      </MetaRow>
    </div>
  );
}

function DetailSkeleton() {
  return (
    <div className="flex flex-1 flex-col gap-3 p-4">
      <Skeleton className="h-10 w-64" />
      <Skeleton className="min-h-[24rem] flex-1" />
    </div>
  );
}
