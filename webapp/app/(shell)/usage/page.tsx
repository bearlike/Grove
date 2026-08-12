import type { Metadata } from "next";

import { ShellHeader } from "@/components/grove/shell/shell-header";
import { sectionFor } from "@/components/grove/shell/nav";
import { UsageAudit } from "@/components/grove/usage";

/** From `NAV_ITEMS`, like every other destination — see `app/(shell)/page.tsx`. */
export const metadata: Metadata = { title: sectionFor("/usage").label };

/** The usage audit. Owned by the usage workstream. */
export default function UsagePage(): React.ReactNode {
  return (
    <>
      <ShellHeader title="Usage" />
      <UsageAudit />
    </>
  );
}
