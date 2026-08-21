import type { Metadata } from "next";

import { LaunchSurface } from "@/components/grove/launch";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { sectionFor } from "@/components/grove/shell/nav";

/** The launch surface's title comes from the shell destination census. */
export const metadata: Metadata = { title: sectionFor("/").label };

/** The default route starts a workspace. */
export default function LaunchPage() {
  return (
    <>
      <ShellHeader />
      <LaunchSurface />
    </>
  );
}
