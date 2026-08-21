import type { Metadata } from "next";

/**
 * Public links live outside `(shell)`: a visitor has not paired with this host,
 * and the capability route must not mount either its authenticated rail or its
 * host-wide event stream. The page itself supplies the small public chrome.
 */
export const metadata: Metadata = {
  title: "Shared workspace",
  description: "A read-only Grove workspace transcript and work summary.",
};

export default function PublicWorkspaceLayout({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  return children;
}
