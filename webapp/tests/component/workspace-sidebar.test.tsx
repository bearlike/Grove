import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { formatUptime, WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { useUiStore } from "@/lib/grove/ui-store";
import { workspace } from "@/tests/_helpers/activity-fixtures";
import type { DashboardSnapshotView, WorkspaceActivityView } from "@/lib/grove/types";

// The rail (ADE #140) is now a session-tree navigator: its scrollable BODY is
// the `SessionRail` (project → date session tree), the search box at top writes
// the one `query` store field (filtering the tree AND the Overview grid), and
// the daemon/identity footer stays at the bottom. This test pins the SHELL
// contract — search, footer, the collapsed icon rail — and the rail body's own
// tree behavior lives in `session-rail.test.tsx`. `usePathname`/`useSearchParams`
// are overridden because the mounted `SessionRail` reads them for the active row.

vi.mock("next/navigation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next/navigation")>();
  return {
    ...actual,
    usePathname: () => "/",
    useSearchParams: () => new URLSearchParams(),
  };
});

/** Place a workspace under a named repo, overriding the fixture's default root. */
function inRepo(w: WorkspaceActivityView, repo_root: string): WorkspaceActivityView {
  return { ...w, state: { ...w.state, repo_root } };
}

/** A two-project snapshot: Grove (2 ws) and website (1 ws). */
function buildSnapshot(): DashboardSnapshotView {
  const grove = [
    inRepo(workspace("g1", "working"), "/repos/Grove"),
    inRepo(workspace("g2", "idle"), "/repos/Grove"),
  ];
  const site = [inRepo(workspace("s1", "blocked"), "/repos/website")];
  const all = [...grove, ...site];
  return {
    projects: [
      { repo_root: "/repos/Grove", repo_name: "Grove", cwd: "/repos/Grove", workspaces: grove },
      { repo_root: "/repos/website", repo_name: "website", cwd: "/repos/website", workspaces: site },
    ],
    generated_at: "2026-06-01T00:00:00Z",
    total_workspaces: all.length,
    needs_attention: all.filter((w) => w.needs_attention).length,
  };
}

const WHOAMI = {
  version: "0.1.0",
  started_at: "2026-05-09T10:00:00Z",
  uptime_seconds: 10,
  host: "grove-host",
  user: "kk",
  platform: "linux",
  python_version: "3.12.7",
};

function renderSidebar({
  snapshot = buildSnapshot(),
  whoami = WHOAMI as unknown,
}: { snapshot?: DashboardSnapshotView; whoami?: unknown } = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  });
  // `useActivityStream` falls back to the `["activity"]` poll under jsdom.
  if (snapshot !== undefined) qc.setQueryData(["activity"], snapshot);
  if (whoami !== undefined) qc.setQueryData(["whoami"], whoami);
  // The rail body reads per-project session lists — seed them empty so the tree
  // mounts without hitting the network (this test isn't about the rows).
  for (const p of snapshot?.projects ?? []) qc.setQueryData(["project-sessions", p.repo_root], []);
  return render(
    <QueryClientProvider client={qc}>
      <WorkspaceSidebar />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUiStore.setState({ query: "", scopeRepo: null });
});

describe("WorkspaceSidebar", () => {
  it("mounts the session-tree body and a search box at the top", async () => {
    renderSidebar();
    expect(await screen.findByTestId("session-rail")).toBeInTheDocument();
    expect(screen.getByTestId("sidebar-search")).toBeInTheDocument();
    // It is a session navigator now — no scope/state filter controls remain.
    expect(screen.queryByTestId("sidebar-repo-all")).toBeNull();
    expect(screen.queryByTestId("sidebar-state-chip")).toBeNull();
    expect(screen.queryByTestId("sidebar-attention-toggle")).toBeNull();
  });

  it("writes the search query to the store", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.type(screen.getByTestId("sidebar-search"), "dash");
    expect(useUiStore.getState().query).toBe("dash");
  });

  it("never renders a collapsed icon-strip variant (collapse hides the whole rail — #152)", async () => {
    // Collapse is a shell concern now (the layout animates the wrapper to w-0);
    // the sidebar always renders its full form, and the old icon strip is gone.
    renderSidebar();
    expect(await screen.findByTestId("session-rail")).toBeInTheDocument();
    expect(screen.queryByTestId("session-rail-icons")).toBeNull();
    expect(screen.queryByTestId("sidebar-footer-collapsed")).toBeNull();
  });

  it("pins the daemon user identity in the footer", async () => {
    renderSidebar();
    const footer = await screen.findByTestId("sidebar-footer");
    expect(footer).toHaveTextContent("kk@grove-host");
  });

  it("re-homes the deleted status bar's system context into the rail footer (#138)", async () => {
    renderSidebar();
    const status = await screen.findByTestId("daemon-status");
    expect(status).toHaveTextContent("online");
    expect(screen.getByTestId("daemon-uptime")).toHaveTextContent(/up \d/);
    expect(screen.getByTestId("daemon-version")).toHaveTextContent("v0.1.0");
    // Count comes off the snapshot facets (3 workspaces in the fixture).
    expect(status.closest("div")).toHaveTextContent("3 ws");
  });

  it("shows the amber update nudge only when a newer release exists (#80, re-homed)", async () => {
    renderSidebar({
      whoami: { ...WHOAMI, latest_version: "0.2.0", update_available: true } as unknown,
    });
    const link = await screen.findByTestId("update-available");
    expect(link).toHaveTextContent("v0.2.0");
    expect(link).toHaveAttribute("href", "https://github.com/bearlike/Grove/releases/latest");
  });
});

describe("formatUptime", () => {
  it.each([
    [0, "0s"],
    [-5, "0s"],
    [5, "5s"],
    [65, "1m 5s"],
    [120, "2m"],
    [3700, "1h 1m"],
    [7200, "2h"],
    [90061, "1d 1h"],
    [172800, "2d"],
  ])("formats %i s as %s", (seconds, expected) => {
    expect(formatUptime(seconds)).toBe(expected);
  });
});
