import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Skeleton } from "@/components/ui/skeleton";

/** The fleet dashboard's instant paint matches its loading card grid. */
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
