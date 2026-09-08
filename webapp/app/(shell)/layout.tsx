import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { OnboardingTour } from "@/components/grove/onboarding";
import { AppShell } from "@/components/grove/shell/app-shell";
import { COOKIE_NAME, sharedCookieStore } from "@/lib/auth/cookie-store";
import { GroveStreamProvider } from "@/lib/grove/hooks";
import { ToolIconsProvider } from "@/lib/grove/tool-icons";
import { readToolIcons } from "@/lib/grove/tool-icons.server";

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
export default async function ShellLayout({ children }: { children: React.ReactNode }) {
  // Middleware rejects a missing cookie before this layout runs. Resolve it again
  // before serializing host configuration so a stale or revoked cookie cannot.
  const cookieId = (await cookies()).get(COOKIE_NAME)?.value;
  if (!cookieId || !(await sharedCookieStore().lookup(cookieId))) redirect("/login");
  const toolIcons = await readToolIcons();

  return (
    <ToolIconsProvider value={toolIcons}>
      <GroveStreamProvider>
        {/*
          The tour wraps the shell rather than a page: its anchors span the rail
          AND the landing composer, and its trigger lives in the rail's account
          menu, so the one mount that sees all three is this layout.
        */}
        <OnboardingTour>
          <AppShell>{children}</AppShell>
        </OnboardingTour>
      </GroveStreamProvider>
    </ToolIconsProvider>
  );
}
