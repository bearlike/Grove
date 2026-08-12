import { ShellHeader } from "@/components/grove/shell/shell-header";
import { TranscriptSkeleton } from "@/components/grove/workspace/transcript-skeleton";

/**
 * `SessionPage` already wraps its own client-side data fetch in a
 * `<Suspense fallback={<TranscriptSkeleton />}>` (see `./page.tsx`), so this
 * file only covers the gap in front of that: the route-segment request
 * itself, before the client component has even mounted. Same fallback, same
 * reason `w/[id]/loading.tsx` copies `Workspace`'s — one shape for "a
 * transcript is coming" everywhere it is shown.
 *
 * Title is the static "Session" the segment's own `layout.tsx` metadata
 * already commits to — this route can never name the session honestly (see
 * that layout's docstring), so there is nothing more specific to fall back
 * to here either.
 */
export default function Loading() {
  return (
    <>
      <ShellHeader title="Session" />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <TranscriptSkeleton />
      </main>
    </>
  );
}
