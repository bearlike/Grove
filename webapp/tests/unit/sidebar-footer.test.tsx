import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  DaemonServiceStrip,
  UpdateHint,
} from "@/components/grove/shell/daemon-status";
import type { WhoamiView } from "@/lib/grove/api";

const sidebar = readFileSync("components/grove/shell/app-sidebar.tsx", "utf8");
const account = readFileSync("components/grove/account/index.tsx", "utf8");

function identity(patch: Partial<WhoamiView> = {}): WhoamiView {
  return {
    version: "0.4.2",
    started_at: "2026-08-10T09:00:00Z",
    uptime_seconds: 3600,
    host: "example-host",
    user: "example-user",
    platform: "Linux-x86_64",
    python_version: "3.13.0",
    latest_version: null,
    update_available: false,
    ...patch,
  } as WhoamiView;
}

describe("sidebar footer", () => {
  it("keeps the existing rail-route census in two focused footer layouts", () => {
    const routeCensus = sidebar.slice(
      sidebar.indexOf("RAIL_ITEMS.map"),
      sidebar.indexOf("</div>", sidebar.indexOf("RAIL_ITEMS.map")),
    );

    expect(routeCensus).toContain("RAIL_ITEMS.map");
    expect(sidebar).toContain('"flex flex-col gap-1.5"');
    expect(sidebar).toContain('"grid grid-cols-2 gap-1.5"');
    expect(routeCensus).toContain("<NavRow");
  });

  it("keeps one native identity tooltip instead of stacking it with the identity value title", () => {
    expect(account).toContain("<TooltipContent");
    expect(account).not.toContain("title={`${user}@${host}`}");
  });

  it("makes the collapsed update control discoverable through native hover and focus help", () => {
    const html = renderToStaticMarkup(<UpdateHint version="0.5.0" collapsed />);

    expect(html).toContain('data-slot="tooltip-trigger"');
    expect(html).toContain('aria-label="Update to 0.5.0"');
  });

  it("renders version and accruing uptime on one labelled baseline with native tooltips", () => {
    const html = renderToStaticMarkup(
      <DaemonServiceStrip identity={identity()} />,
    );

    expect(html).toContain('data-testid="daemon-service-strip"');
    expect(html).toContain("items-baseline");
    expect(html).toContain('data-testid="daemon-version"');
    expect(html).toContain('data-testid="uptime"');
    expect(html).toContain("max-w-28 shrink-0 truncate");
    expect(html).not.toContain("Running since");
    expect(html.match(/data-slot="tooltip-trigger"/g)).toHaveLength(2);
    expect(html).not.toContain("font-mono");
  });
});
