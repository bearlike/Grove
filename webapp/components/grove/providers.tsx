"use client";

import { useState } from "react";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { toast } from "sonner";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { GroveProtocolError } from "@/lib/grove/api";

/**
 * Grove's data is a live fleet, and the event stream is what keeps it live —
 * see `lib/grove/hooks/stream`. These defaults are read against that.
 *
 * `staleTime` no longer competes with a poll: with every interval gated on the
 * stream, it decides only whether a REMOUNT refetches. Five seconds was shorter
 * than the daemon's own ~2 s delta cadence, so switching a work-panel tab
 * re-fetched data the stream had already delivered. Thirty seconds still
 * refreshes anything genuinely old on the way in.
 *
 * `refetchOnWindowFocus` is back ON for the opposite reason: returning to a
 * backgrounded tab is exactly when the cache is most likely wrong, because an
 * `EventSource` there can die without firing `onerror`. It costs one round of
 * fetches on a real edge, where the intervals cost a round forever.
 */
const STALE_MS = 30_000;

/**
 * One toast id for every generic mutation failure.
 *
 * A daemon outage fails several mutations in the same few seconds (a queued
 * steer, a stale lifecycle click, a retried control) — one rolling toast that
 * updates in place says the same honest thing a stack of five would, without
 * asking the user to dismiss five.
 */
const MUTATION_ERROR_TOAST_ID = "grove-mutation-error";

/**
 * A mutation's error, reduced to one sentence a toast can carry.
 *
 * `GroveProtocolError` already IS the daemon's own explanation (see
 * `lib/grove/api/client.ts`), so it is preferred verbatim over inventing
 * wording here — the same call `lib/grove/runtime/notice.ts` makes for the
 * steering-specific refusals. Exported because the mapping is the contract
 * worth pinning, not the toast call around it.
 */
export function mutationErrorMessage(error: unknown): string {
  if (error instanceof GroveProtocolError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return "Something went wrong.";
}

/**
 * INLINE vs TRANSIENT, decided once, here — the seam every mutation and query
 * in the app now shares without any of them being edited.
 *
 * READS stay inline-only. Every reachable surface already renders its own
 * `isError`/retry per the design system's required Error state (fleet, the
 * sessions catalog, the transcript, ticket refs, peek, the whole usage page),
 * and react-query's own `retry` plus the stream's `backstopInterval` mean a
 * background poll fails and quietly heals far more often than a user should
 * be interrupted for — toasting it would be exactly the noise the daemon
 * connectivity edge below is careful not to be.
 *
 * WRITES toast. A mutation is an event the user just triggered, not a
 * standing fact about data on screen — the same distinction
 * `usage-audit.tsx`'s `refreshNote` draws for its own refresh button. Some
 * writes already print `mutation.error` beside the control that fired them
 * (lifecycle actions, session controls, session remap, steer/interrupt/answer
 * via `notice`); the toast is not a second vocabulary for those, it is the
 * same fact for whoever is not currently looking at that control. The one
 * write that deliberately opts OUT of this — `useRefreshUsage`
 * (`lib/grove/hooks/usage.ts`) — has no key or `meta` flag this seam can read
 * without editing that file, so it is a known, reported double-surface until
 * a `meta: { toast: false }` lands there.
 */
function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: STALE_MS, refetchOnWindowFocus: true, retry: 1 },
    },
    mutationCache: new MutationCache({
      // `meta.toast === false` is the OPT-OUT, and it exists because a global
      // handler cannot see that a caller already renders the failure. A
      // mutation whose own surface states the outcome inline — `useRefreshUsage`
      // writes it into the coverage strip, which is a statement about the data
      // on screen rather than an event — would otherwise say it twice, and two
      // reports of one failure read as two failures.
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.toast === false) return;
        toast.error(mutationErrorMessage(error), { id: MUTATION_ERROR_TOAST_ID });
      },
    }),
  });
}

/**
 * The providers every route needs, INCLUDING the ones nobody has signed in to.
 *
 * The client is created in state rather than at module scope so a server render
 * never shares one across requests.
 *
 * WHAT IS DELIBERATELY NOT HERE: `GroveStreamProvider`. It subscribes to
 * `/events` and warms the fleet cache, which are things only an authenticated
 * surface can do — mounted globally it opened an `EventSource` on the LOGIN
 * screen, where the BFF answers 401 forever. Measured on a clean cookie jar:
 * six connection attempts and four `/activity` fetches in the first four
 * seconds, retrying for as long as the page stayed open. The first screen a new
 * user ever sees was quietly failing at something, in a loop, in their console.
 *
 * It now lives on `app/(shell)/layout.tsx`, because that route group already IS
 * the authenticated boundary. That is the reason the fix is a MOVE and not a
 * `pathname === "/login"` guard in the provider: a route rule buried in a data
 * component is invisible from the routes it governs, and it silently fails to
 * cover the second unauthenticated route somebody adds.
 *
 * The query client stays global on purpose. It is an empty cache until someone
 * runs a query, so it costs a pre-auth page nothing, and keeping it here means a
 * future public route can use react-query without moving providers around again.
 * Ordering still holds: the stream mounts BELOW this, so it can still write into
 * the cache it depends on.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
        <TooltipProvider>
          {children}
          {/* One balloon layer for the app, beside the one query client — the
              same "exactly once" rule `GroveStreamProvider` follows. A manual
              close button because the user must be able to dismiss any toast,
              not only wait it out; sonner's own prop, never a styled call
              site. */}
          <Toaster closeButton />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
