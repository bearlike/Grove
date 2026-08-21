import { BrandMark } from "@/components/grove/brand-mark";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { Skeleton } from "@/components/ui/skeleton";

/** The launch route's instant paint holds its wordmark, heading, and composer geometry. */
export default function Loading() {
  return (
    <>
      <ShellHeader />
      <main
        className="flex min-h-0 flex-1 flex-col"
        aria-busy="true"
        aria-label="Loading launch"
        style={{
          ["--thread-max-width" as string]: "44rem",
          ["--composer-bg" as string]:
            "color-mix(in oklab, var(--color-muted) 30%, var(--color-background))",
          ["--composer-radius" as string]: "1.5rem",
          ["--composer-padding" as string]: "8px",
        }}
      >
        <div className="mx-auto flex w-full max-w-(--thread-max-width) flex-1 flex-col justify-center gap-4 px-4">
          <div className="flex items-center justify-center gap-2 text-sm font-medium">
            <BrandMark className="size-6" />
            <span>Grove</span>
          </div>
          <h1 className="text-content-primary text-center text-2xl font-semibold">
            What would you like to work on?
          </h1>
          <div className="bg-surface-raised border-surface-edge surface-raised flex w-full flex-col gap-2 rounded-(--composer-radius) border p-2">
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
