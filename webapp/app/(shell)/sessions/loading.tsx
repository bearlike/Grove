import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Mirrors `./page.tsx`'s own shape: the search-plus-filter toolbar row, then
 * the table's box. The row count and heights are `SessionsPage`'s local
 * `SessionSkeleton`, copied rather than imported — that component is not
 * exported, and `./page.tsx` is a "use client" page outside this file's
 * owned set, so the shared shape is reproduced here instead of reached into.
 * `min-h-0 flex-1` on the table box is the load-bearing part there too: a
 * skeleton at its natural height would let the real table's arrival shove
 * the count line down instead of only changing what the rows say.
 */
export default function Loading() {
  return (
    <>
      <ShellHeader />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4">
        <div className="flex shrink-0 items-center gap-2">
          <Skeleton className="h-9 min-w-0 flex-1" />
          <Skeleton className="h-9 w-28 shrink-0" />
        </div>
        <div
          className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden"
          role="status"
          aria-label="Loading sessions"
          aria-hidden
        >
          <Skeleton className="h-8 w-full shrink-0" />
          {Array.from({ length: 12 }, (_, index) => (
            <Skeleton key={index} className="h-10 w-full shrink-0" />
          ))}
        </div>
      </main>
    </>
  );
}
