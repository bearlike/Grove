"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useEffect, useState } from "react";
import { makeQueryClient } from "@/lib/grove/query-client";
import { useUiStore } from "@/lib/grove/ui-store";

/**
 * Read the persisted UI store back on the client, once, after mount. The store
 * boots with `skipHydration` so server + first-paint markup render at defaults
 * (no hydration mismatch); this flips to the stored values one frame later.
 */
function StoreHydrator() {
  useEffect(() => {
    void useUiStore.persist.rehydrate();
  }, []);
  return null;
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(() => makeQueryClient());
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <QueryClientProvider client={client}>
        <StoreHydrator />
        <TooltipProvider delayDuration={150}>{children}</TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
