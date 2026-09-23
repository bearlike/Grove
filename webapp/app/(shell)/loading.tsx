import { AppLogo } from "@/components/grove/app-logo";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { paper } from "@/components/elements/surfaces";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

/** The launch route's instant paint holds its wordmark, heading, and composer geometry. */
export default function Loading() {
  return (
    <>
      <ShellHeader />
      <main
        className="flex min-h-0 flex-1 flex-col"
        aria-busy="true"
        aria-label="Loading launch"
        style={{ ["--thread-max-width" as string]: "52rem" }}
      >
        <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col justify-center gap-4 px-4">
          <div className="flex items-center justify-center gap-2 text-sm font-medium">
            {/* The APP ICON, because the page this stands in for draws one:
                a skeleton that swaps logo when the real surface arrives is a
                visible flip on every navigation to the landing route. */}
            <AppLogo className="size-6" />
            <span>Grove</span>
          </div>
          <h1 className="text-content-primary text-center text-2xl font-semibold">
            What would you like to work on?
          </h1>
          {/* The vendored bar's own surface, so the paint does not change look
              when the real composer replaces it. */}
          <div className={cn(paper, "flex w-full flex-col gap-2 rounded-[24px] p-2.5")}>
            <Skeleton className="h-24 w-full" />
            <div className="flex items-center justify-between">
              <Skeleton className="h-7 w-48" />
              <Skeleton className="h-7 w-16" />
            </div>
          </div>
        </div>
      </main>
    </>
  );
}
