import { GallerySkeleton } from "@/components/grove/gallery/gallery";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Skeleton } from "@/components/ui/skeleton";

/** Mirrors `./page.tsx`: the toolbar row, then the grid of card-shaped skeletons. */
export default function Loading() {
  return (
    <>
      <ShellHeader />
      <main className="flex min-h-0 min-w-0 flex-1 flex-col gap-3 p-4">
        <div className="flex shrink-0 items-center gap-2">
          <Skeleton className="h-9 min-w-0 flex-1" />
          <Skeleton className="h-9 w-44 shrink-0" />
          <Skeleton className="h-9 w-36 shrink-0" />
        </div>
        <GallerySkeleton />
      </main>
    </>
  );
}
