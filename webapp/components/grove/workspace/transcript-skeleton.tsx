"use client";

import { Skeleton } from "@/components/ui/skeleton";

/**
 * The transcript's loading shape — a stand-in conversation, not a grey box in
 * the middle of the screen.
 *
 * Grove's transcripts are large and slow to load BY DESIGN (a "48-turn"
 * session is ~4,800 message components — see webapp/CLAUDE.md's "count in
 * messages, not turns"), so this is not a rare flash: it is the ordinary first
 * second of every workspace and session-detail visit. Standing in for the
 * SHAPE of a conversation is what keeps that second from reading as "this
 * session is new" (the vendored `Thread`'s own welcome screen) or "this
 * loaded wrong" — both of which a caller must reach for ONLY once a query has
 * actually resolved, never while it is still pending.
 *
 * Shared by the workspace page's peek-pending state, the transcript pane's own
 * turns-pending state, and the read-only session-detail page — one shape, one
 * place, so the three surfaces cannot drift into three different "loading"
 * looks.
 */
export function TranscriptSkeleton() {
  return (
    <div
      data-testid="transcript-skeleton"
      data-source="loading"
      aria-hidden
      className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-4 pt-6"
    >
      {TURNS.map((role, index) =>
        role === "user" ? (
          <div key={index} className="flex justify-end">
            <Skeleton className="h-9 w-2/5 shrink-0" />
          </div>
        ) : (
          <div key={index} className="flex w-4/5 flex-col gap-2">
            <Skeleton className="h-3.5 w-full" />
            <Skeleton className="h-3.5 w-11/12" />
            <Skeleton className="h-3.5 w-2/3" />
          </div>
        ),
      )}
    </div>
  );
}

/** One plausible turn shape, alternating the way a real exchange does. */
const TURNS = ["user", "assistant", "assistant", "user", "assistant"] as const;
