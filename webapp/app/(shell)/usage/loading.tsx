import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * `UsageAudit`'s six queries each own their OWN loading state once mounted
 * (`UsageSection`'s `loading` switch, per card) — this file only stands in
 * for the gap before that client component exists at all, so it does not
 * try to reproduce every card's title or icon. It DOES copy the page's own
 * grid shape row for row (`usage-audit.tsx`'s `main`), because that shape,
 * not any one card's content, is what "the frame does not move" is about
 * here.
 */
export default function Loading() {
  return (
    <>
      <ShellHeader title="Usage" />
      <main
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4"
        role="status"
        aria-label="Loading usage"
        aria-hidden
      >
        <div className="grid min-w-0 gap-4 xl:grid-cols-3">
          <Skeleton className="h-40 w-full xl:col-span-2" />
          <Skeleton className="h-40 w-full" />
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((tile) => (
            <Skeleton key={tile} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-52 w-full" />
        <div className="grid min-w-0 gap-4 xl:grid-cols-11">
          <Skeleton className="h-72 w-full xl:col-span-7" />
          <Skeleton className="h-72 w-full xl:col-span-4" />
        </div>
        <div className="grid min-w-0 gap-4 xl:grid-cols-3">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full xl:col-span-2" />
        </div>
        <Skeleton className="h-56 w-full" />
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-96 w-full" />
      </main>
    </>
  );
}
