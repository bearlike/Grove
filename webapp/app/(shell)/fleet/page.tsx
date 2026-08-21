import type { Metadata } from "next";

import { FleetDashboard } from "@/components/grove/fleet";
import { ShellHeader } from "@/components/grove/shell/shell-header";
import { sectionFor } from "@/components/grove/shell/nav";

/** The fleet title comes from the shell destination census. */
export const metadata: Metadata = { title: sectionFor("/fleet").label };

/** The fleet dashboard. Owned by the fleet workstream. */
export default function FleetPage() {
  return (
    <>
      <ShellHeader />
      <div className="min-h-0 flex-1 overflow-auto p-4" data-testid="fleet-page">
        <FleetDashboard />
      </div>
    </>
  );
}
