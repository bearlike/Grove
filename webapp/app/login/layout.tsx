import type { Metadata } from "next";

/** `page.tsx` is a client component and cannot export metadata; this carries it
 * and renders its children untouched. "Pair" rather than "Sign in" because that
 * is what the screen does and what its button says — there is no password here,
 * there is a code you approve on the host. */
export const metadata: Metadata = { title: "Pair this device" };

export default function LoginLayout({
  children,
}: {
  children: React.ReactNode;
}): React.ReactNode {
  return children;
}
