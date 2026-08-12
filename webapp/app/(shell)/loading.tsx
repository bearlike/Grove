import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * The fleet dashboard's instant paint — Next's Suspense fallback for this
 * segment, shown the moment navigation starts rather than after the round
 * trip that used to hold the previous page frozen on screen.
 *
 * Mirrors `app/(shell)/page.tsx` box for box: `ShellHeader` with no title (it
 * already falls back to "Fleet" via `sectionFor`) and the same `p-4
 * overflow-auto` slot `FleetDashboard` renders into. The card grid below
 * copies `FleetDashboard`'s OWN `query.isPending` skeleton (four `h-40`
 * tiles in the identical `auto-fill` grid) rather than inventing a second
 * shape, so the frame does not move a second time once the client component
 * mounts and takes over its own loading state.
 */
export default function Loading() {
  return (
    <>
      <ShellHeader />
      <div className="min-h-0 flex-1 overflow-auto p-4">
        <div
          className="flex flex-col gap-5"
          role="status"
          aria-label="Loading workspaces"
          aria-hidden
        >
          <div className="flex flex-wrap items-center gap-2">
            <Skeleton className="h-9 w-full max-w-xs" />
            <Skeleton className="h-9 w-24 shrink-0" />
            <Skeleton className="ml-auto h-9 w-32 shrink-0" />
          </div>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(22rem,1fr))] gap-3">
            {[0, 1, 2, 3].map((card) => (
              <Skeleton key={card} className="h-40 w-full" />
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
