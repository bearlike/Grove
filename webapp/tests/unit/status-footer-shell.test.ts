import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/**
 * Where the status footer is MOUNTED, and where it must never be (#814).
 *
 * Source assertions, for `app-shell.test.ts`'s reason: these are JSX structure
 * and Tailwind classes, so there is nothing pure to import. The contract here
 * is containment — which trees carry the band at all — and a census is the
 * only thing that can hold an absence.
 */
const shell = readFileSync("components/grove/shell/app-shell.tsx", "utf8");
const publicView = readFileSync(
  "components/grove/public/public-workspace.tsx",
  "utf8",
);
const login = readFileSync("app/login/page.tsx", "utf8");
const shellLayout = readFileSync("app/(shell)/layout.tsx", "utf8");

describe("the footer spans the whole app, beneath the rail and the page", () => {
  it("wraps the rail+page row in a column so the band is a sibling of both", () => {
    // A row cannot host a full-width bottom band. The column is the outer
    // element and the row becomes its first child.
    expect(shell).toContain("flex h-dvh w-full flex-col overflow-hidden");
  });

  it("keeps `relative` on the ROW, which is what contains the rail", () => {
    // `webapp/CLAUDE.md` records the trap: a page positioning itself
    // `absolute inset-0` resolves against the nearest positioned ancestor, and
    // if that became the column the page would paint over the brand. The row
    // keeps `relative`; the column must not take it.
    expect(shell).toMatch(/relative flex min-h-0 w-full flex-1 overflow-hidden/);
    expect(shell).not.toMatch(/relative flex h-dvh w-full flex-col/);
  });

  it("renders the band outside the page panel, so no page can scroll it away", () => {
    const panelAt = shell.indexOf("shell-panel");
    const footerAt = shell.indexOf("<StatusFooter");
    expect(panelAt).toBeGreaterThan(-1);
    expect(footerAt).toBeGreaterThan(panelAt);
  });
});

describe("the band is contained to the authenticated shell", () => {
  /**
   * A share link hands a stranger one workspace. Fleet counts, account
   * identities and subscription headroom are all facts about the HOST, so the
   * containment is structural rather than a prop: the public view and the
   * login page roll their own roots and never mount `AppShell`.
   */
  it("mounts AppShell only from the authenticated route group", () => {
    expect(shellLayout).toContain("<AppShell>");
  });

  it("never reaches the public share view", () => {
    expect(publicView).not.toContain("StatusFooter");
    expect(publicView).not.toContain("<AppShell");
  });

  it("never reaches the login page", () => {
    expect(login).not.toContain("StatusFooter");
    expect(login).not.toContain("<AppShell");
  });
});

describe("the footer reads the shell's existing queries", () => {
  it("opens no second event stream", () => {
    // One `EventSource` per APP is the shell's own rule. The footer takes the
    // snapshot the rail already holds.
    expect(shell.match(/useFleetStream\(\)/g) ?? []).toHaveLength(1);
  });

  it("derives context from the route, never from both sources at once", () => {
    // Mixing one project's name with another workspace's branch is the one
    // thing the band must not do, so exactly one branch runs.
    expect(shell).toContain("workspaceContext");
    expect(shell).toContain("projectContext");
    expect(shell).toContain('pathname.startsWith("/w/")');
  });

  it("refuses to render counts from a disconnected stream", () => {
    // A dead stream holds whatever it last saw; publishing that as a live
    // count is the silently-stale answer the footer contract refuses.
    expect(shell).toContain("connected ? fleetCounts(stream.snapshot) : EMPTY_COUNTS");
  });
});
