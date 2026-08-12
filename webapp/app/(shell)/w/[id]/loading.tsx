import { ShellHeader } from "@/components/grove/shell/shell-header";
import { TranscriptSkeleton } from "@/components/grove/workspace/transcript-skeleton";

/**
 * Covers exactly the wait `generateMetadata` in `./page.tsx` introduces.
 *
 * That function awaits `cookies()` plus a `cache: "no-store"` fetch to the
 * daemon before the route can render at all — a real server round trip on
 * every navigation here, by design (see its own docstring for why it is not
 * removed). `loading.js` wraps `page.js` in a Suspense boundary, so this is
 * what the visitor sees for that round trip instead of the previous page
 * staying on screen.
 *
 * The shape is `Workspace`'s OWN `!peek.data` branch, copied rather than
 * guessed: `ShellHeader` plus `TranscriptSkeleton`, the same pairing
 * `components/grove/workspace/index.tsx` renders while its peek query is
 * still pending. Two different waits, one look, so nothing jumps between
 * them. The title falls back to "Workspace" — the same noun
 * `generateMetadata` itself falls back to — never the id: this file has no
 * params to read, and an id in a tab or a screenshot is the thing that route
 * exists to prevent.
 */
export default function Loading() {
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <ShellHeader title="Workspace" />
      <TranscriptSkeleton />
    </div>
  );
}
