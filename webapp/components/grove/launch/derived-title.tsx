"use client";

import { deriveTitle } from "@/lib/grove/adapters/launch";

/**
 * Makes the generated workspace name visible before the user launches it.
 *
 * Deriving a name and never showing it is how somebody ends up with five
 * workspaces called something they did not write.
 *
 * The prompt arrives as a prop rather than from a composer store: this surface
 * holds its text in plain React state, which is what let it drop the
 * assistant-ui runtime entirely.
 */
export function LaunchDerivedTitle({ prompt }: { readonly prompt: string }): React.ReactNode {
  if (!prompt.trim()) return null;

  return (
    <p className="text-content-tertiary text-sm">will be called &quot;{deriveTitle(prompt)}&quot;</p>
  );
}
