import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const sidebar = readFileSync("components/grove/shell/app-sidebar.tsx", "utf8");
const account = readFileSync("components/grove/account/index.tsx", "utf8");

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

  /**
   * The service strip MOVED to the status footer (#814) — it was not deleted
   * and it must not be duplicated. Pinned as an absence here because an absent
   * mechanism has no runtime artifact to assert against, and re-mounting it in
   * the rail is exactly the regression that would put two copies of the
   * version on one screen.
   */
  it("renders no daemon service facts of its own", () => {
    // MUTATION-TESTED. Grepping the source for `DaemonStatus` SURVIVED a
    // re-mount, because the word also appears in prose and in comments — a
    // token census cannot tell a mention from a mount. Assert on the IMPORT
    // (a component cannot be rendered without one) and on the daemon-fact
    // queries, which is what a re-implementation would have to reach for.
    expect(sidebar).not.toMatch(/import\s*{[^}]*DaemonStatus[^}]*}\s*from/);
    expect(sidebar).not.toContain("useWhoami");
    expect(sidebar).not.toContain("daemon-service-strip");
    expect(sidebar).not.toContain("started_at");
  });
});
