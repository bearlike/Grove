"use client";

import { AssistantRuntimeProvider } from "@assistant-ui/react";
import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { EmptyState, EmptyStateGreeting } from "@/components/elements/empty-state";
import { ErrorState } from "@/components/elements/error-state";
import { duration } from "@/components/grove/duration";
import { Explain } from "@/components/grove/glossary";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { GroveDataParts } from "@/components/grove/workspace/data-parts";
import { Thread, type ThreadComponents } from "@/components/grove/workspace/thread";
import { GROVE_THREAD_COMPONENTS } from "@/components/grove/workspace/tool-call-part";
import { TranscriptSkeleton } from "@/components/grove/workspace/transcript-skeleton";
import { Badge } from "@/components/ui/badge";
import type { DurationView } from "@/lib/grove/api";
import { useCatalogTurns } from "@/lib/grove/hooks";
import { sessionTitle, useReadOnlyTranscript } from "@/lib/grove/runtime";

/**
 * `GROVE_THREAD_COMPONENTS` with the vendored "new chat" welcome suppressed.
 *
 * Same fix as `components/grove/workspace/transcript.tsx`, needed here for
 * the same reason: `Thread`'s welcome reads assistant-ui's internal runtime
 * state, which syncs from the external store's `messages` prop via an effect
 * rather than synchronously, and visibly lags on a large transcript. `Thread`
 * only mounts below once `isEmpty` (derived straight from `transcript.data`,
 * never from the runtime) is already known false, so the vendored welcome
 * would never be honest here — it always loses the race to real data this
 * page already has.
 */
const SUPPRESSED_WELCOME_COMPONENTS: ThreadComponents = {
  ...GROVE_THREAD_COMPONENTS,
  Welcome: () => null,
};

/**
 * An archived catalog session: the SAME transcript the workspace renders, with
 * nothing to steer.
 *
 * It renders `Thread` and `GroveDataParts` — the workspace's own two pieces —
 * rather than a stripped-down copy. A private `MessagePrimitive.Parts` with no
 * `components` prop used to live here, and every part assistant-ui has no
 * native renderer for (tool calls, file edits, todo lists, reasoning,
 * questions, status notes) fell through to plain text: an archived session read
 * as a wall of raw strings while the same turns rendered properly one route
 * away. One transcript renderer is the fix; a second one is the bug.
 *
 * WHAT LEGITIMATELY DIFFERS, and how each is expressed:
 *   - Read-only. `useReadOnlyTranscript` sets the runtime's own `isDisabled`
 *     capability, so the composer is structurally absent rather than hidden.
 *     No workspace id is faked to get there.
 *   - No work panel, no lifecycle, no phase, no live question. All of those are
 *     keyed to a workspace this route does not have; a question that WAS asked
 *     still renders, in its historical form, because `GroveDataParts` maps it
 *     to `HistoricalQuestion` rather than to the interactive card.
 *   - Coordinates. `kind` and `cwd` ride the query string and can be absent —
 *     ~2% of transcripts never recorded a cwd — so `MissingCoordinate` is a
 *     real state, not a defensive branch.
 *
 */
export default function SessionPage(): React.ReactNode {
  // `useSearchParams` needs a Suspense boundary or the route's static prerender
  // fails the build — the same wrapper `app/login/page.tsx` carries, for the
  // same reason. The coordinates it reads are the session's IDENTITY here, so
  // there is no rendering anything above it while they are unknown.
  return (
    <Suspense fallback={<TranscriptSkeleton />}>
      <SessionView />
    </Suspense>
  );
}

function SessionView(): React.ReactNode {
  const params = useParams<{ id: string }>();
  const search = useSearchParams();
  const sessionId = params.id ?? null;
  const kind = search.get("kind");
  const cwd = search.get("cwd");
  const transcript = useCatalogTurns(sessionId, kind, cwd);
  const { runtime, isEmpty } = useReadOnlyTranscript(transcript.data?.turns);
  const title = transcript.data ? sessionTitle(transcript.data.session) : sessionId ?? "Session";

  return (
    <>
      <ShellHeader
        title={title}
        actions={
          transcript.data ? (
            <SessionDurationBadges duration={transcript.data.session.duration} />
          ) : undefined
        }
      />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="session-page">
        {kind === null || cwd === null ? <MissingCoordinate /> : null}
        {kind !== null && cwd !== null && transcript.isLoading ? <TranscriptSkeleton /> : null}
        {transcript.error ? <ErrorState className="m-4" title="Couldn’t load transcript" detail={transcript.error.message} retrying={transcript.isFetching} onRetry={() => void transcript.refetch()} /> : null}
        {!transcript.isLoading && !transcript.error && isEmpty ? <EmptyTranscript /> : null}
        {!transcript.isLoading && !transcript.error && !isEmpty ? (
          <AssistantRuntimeProvider runtime={runtime}>
            <GroveDataParts />
            {/* The same wrapper the workspace pane gives the thread: `Thread`'s
                root is `h-full`, so it needs a flex child with a bounded height
                to resolve against. Width and inset are the thread's own
                (`THREAD_WIDTH` / `THREAD_INSET`) — this route sets neither, so
                it inherits the shared column edge by construction. */}
            <div className="flex min-h-0 min-w-0 flex-1 flex-col" data-testid="transcript">
              {/* The workspace pane's overrides, verbatim: one transcript
                  renderer is the fix, a second one is the bug (above).
                  `isEmpty` is always false in this branch (see above), so
                  this always picks the suppressed welcome — spelled as a
                  ternary anyway so it stays correct if that branch shape
                  ever changes. */}
              <Thread
                components={isEmpty ? GROVE_THREAD_COMPONENTS : SUPPRESSED_WELCOME_COMPONENTS}
              />
            </div>
          </AssistantRuntimeProvider>
        ) : null}
      </main>
    </>
  );
}

/**
 * The same two figures the session tables show, spelled out here because this
 * page is the one place a reader opens deliberately to look at ONE session.
 *
 * Rendered unconditionally once the session is loaded — even while the
 * backend that fills `duration` is still landing and every row reads
 * `not measured` — so the missing-data state looks like a deliberate "not
 * measured yet" rather than a chip that silently failed to appear. Never
 * merged into one figure: Wall clock (the union of the session's active
 * intervals) and Compute (the same intervals summed across every sub-agent)
 * answer different questions, see `components/grove/duration.ts`.
 */
function SessionDurationBadges({
  duration: sessionDuration,
}: {
  duration: DurationView | null | undefined;
}): React.ReactNode {
  return (
    <>
      {/* The one-line definition behind each word is the shared glossary
          entry (`components/grove/glossary.tsx`), not a `title` attribute —
          same vocabulary as the session tables. */}
      <Badge variant="outline">
        <Explain term="clock_time">Wall clock</Explain> {duration(sessionDuration?.active_ms)}
      </Badge>
      <Badge variant="outline">
        <Explain term="compute_time">Compute</Explain>{" "}
        {duration(sessionDuration?.execution_ms)}
      </Badge>
    </>
  );
}

function MissingCoordinate(): React.ReactNode {
  return <ErrorState className="m-4" title="Transcript unavailable" detail="This session did not record a location, so Grove cannot open it." retrying={false} onRetry={() => undefined} />;
}

function EmptyTranscript(): React.ReactNode {
  return <EmptyState className="mx-auto my-auto"><EmptyStateGreeting>No messages recorded</EmptyStateGreeting></EmptyState>;
}
