import { AppShell } from "@/components/grove/shell/app-shell";
import { GroveStreamProvider } from "@/lib/grove/hooks";

/**
 * Every authenticated surface renders inside the one shell — and inside the one
 * event stream.
 *
 * THE STREAM IS MOUNTED HERE BECAUSE THIS GROUP IS THE AUTH BOUNDARY. `/login`
 * sits outside `(shell)` by construction, so scoping the subscription to this
 * layout is what stops a pre-auth screen opening an `EventSource` the BFF can
 * only answer 401 to. Nothing about that rule lives in the provider, which means
 * the next unauthenticated route gets the correct behaviour for free rather than
 * needing to be added to a list.
 *
 * It still sits below the app-wide `QueryClientProvider` (root layout), which is
 * the ordering it needs: it writes the fleet snapshot into that cache.
 *
 * "Exactly once" is preserved and is now structural in a second way — this
 * layout is a single mount point for every authenticated route, so navigating
 * between them re-uses one connection instead of opening a second.
 */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return (
    <GroveStreamProvider>
      <AppShell>{children}</AppShell>
    </GroveStreamProvider>
  );
}
