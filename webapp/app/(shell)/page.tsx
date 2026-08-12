import type { Metadata } from "next";

import { ShellHeader } from "@/components/grove/shell/shell-header";
import { sectionFor } from "@/components/grove/shell/nav";
import { FleetDashboard } from "@/components/grove/fleet";

/**
 * The title comes from `sectionFor`, not from a string typed here.
 *
 * `NAV_ITEMS` is already the one census of what a destination is CALLED — the
 * rail row and the page header both read `label` from it — and a tab title
 * typed separately would make three names for one place, which is the exact
 * failure that list's docstring exists to prevent. `sectionFor` also already
 * answers "which section is this path", so there is nothing to add.
 */
export const metadata: Metadata = { title: sectionFor("/").label };

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
