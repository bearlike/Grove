import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkspaceSidebar } from "@/components/layout/workspace-sidebar";
import { useUiStore } from "@/lib/grove/ui-store";
import { workspace } from "@/tests/_helpers/activity-fixtures";
import type { DashboardSnapshotView, WorkspaceActivityView } from "@/lib/grove/types";

// The rail is purely a view-intent surface (#96 deliverable B): it reads the
// authoritative `/activity` snapshot (via `useActivityStream`, which in jsdom has
// no EventSource and so falls back to the `["activity"]` poll) and writes the one
// Zustand UI store. No router is needed — the rail no longer links to detail
// pages (cards do the navigation now).

/** Place a workspace under a named repo, overriding the fixture's default root. */
function inRepo(
  w: WorkspaceActivityView,
  repo_root: string,
): WorkspaceActivityView {
  return { ...w, state: { ...w.state, repo_root } };
}

/** A two-project snapshot: Grove (2 ws: working + idle) and website (1 ws: blocked). */
function buildSnapshot(): DashboardSnapshotView {
  const grove = [
    inRepo(workspace("g1", "working"), "/repos/Grove"),
    inRepo(workspace("g2", "idle"), "/repos/Grove"),
  ];
  const site = [inRepo(workspace("s1", "blocked"), "/repos/website")];
  const all = [...grove, ...site];
  return {
    projects: [
      { repo_root: "/repos/Grove", repo_name: "Grove", workspaces: grove },
      { repo_root: "/repos/website", repo_name: "website", workspaces: site },
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
  return render(
    <QueryClientProvider client={qc}>
      <WorkspaceSidebar />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  // The store is a module-level singleton; reset the view slices each test so
  // toggles don't leak across cases.
  useUiStore.setState({
    query: "",
    scopeRepo: null,
    hiddenStates: [],
    attentionOnly: false,
  });
});

describe("WorkspaceSidebar", () => {
  it("renders a repo scope list with counts — All + one row per project, no workspace rows", async () => {
    renderSidebar();
    // "All workspaces" totals the snapshot; each repo row carries its count.
    const all = await screen.findByTestId("sidebar-repo-all");
    expect(all).toHaveTextContent("All workspaces");
    expect(all).toHaveTextContent("3");

    const repos = screen.getAllByTestId("sidebar-repo");
    expect(repos.map((r) => r.getAttribute("data-repo"))).toEqual([
      "/repos/Grove",
      "/repos/website",
    ]);
    expect(repos[0]).toHaveTextContent("Grove");
    expect(repos[0]).toHaveTextContent("2");
    expect(repos[1]).toHaveTextContent("website");
    expect(repos[1]).toHaveTextContent("1");

    // It is a nav/scope rail now — no per-workspace rows and no detail links.
    expect(screen.queryByTestId("sidebar-entry")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("scopes the store to a repo on click and marks the active row", async () => {
    const user = userEvent.setup();
    renderSidebar();
    const groveRow = (await screen.findAllByTestId("sidebar-repo"))[0];
    await user.click(groveRow);
    expect(useUiStore.getState().scopeRepo).toBe("/repos/Grove");
    expect(groveRow).toHaveAttribute("aria-pressed", "true");
    // "All workspaces" is active by default until a repo is chosen.
    await user.click(screen.getByTestId("sidebar-repo-all"));
    expect(useUiStore.getState().scopeRepo).toBeNull();
    expect(screen.getByTestId("sidebar-repo-all")).toHaveAttribute("aria-pressed", "true");
  });

  it("writes the search query to the store", async () => {
    const user = userEvent.setup();
    renderSidebar();
    await user.type(screen.getByTestId("sidebar-search"), "dash");
    expect(useUiStore.getState().query).toBe("dash");
  });

  it("renders an agent-state chip per present state and toggles hiddenStates", async () => {
    const user = userEvent.setup();
    renderSidebar();
    const chips = await screen.findAllByTestId("sidebar-state-chip");
    const present = chips.map((c) => c.getAttribute("data-state-key"));
    // working, idle, blocked are present in the fixture (order is STATE_ORDER).
    expect(present).toEqual(["working", "blocked", "idle"]);

    const working = chips.find((c) => c.getAttribute("data-state-key") === "working")!;
    // A chip starts "on" (state visible → not hidden).
    expect(working).toHaveAttribute("aria-pressed", "true");
    await user.click(working);
    expect(useUiStore.getState().hiddenStates).toContain("working");
    expect(working).toHaveAttribute("aria-pressed", "false");
  });

  it("toggles attention-only", async () => {
    const user = userEvent.setup();
    renderSidebar();
    const toggle = await screen.findByTestId("sidebar-attention-toggle");
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    // One blocked workspace → attention count of 1.
    expect(toggle).toHaveTextContent("1");
    await user.click(toggle);
    expect(useUiStore.getState().attentionOnly).toBe(true);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
  });

  it("no longer renders a Workspaces/Activity route nav", () => {
    renderSidebar();
    expect(screen.queryByTestId("sidebar-nav-active")).toBeNull();
    expect(screen.queryByRole("navigation", { name: "Primary" })).toBeNull();
  });

  it("pins the daemon user identity in the footer, without the version", async () => {
    renderSidebar();
    const footer = await screen.findByTestId("sidebar-footer");
    expect(footer).toHaveTextContent("kk@grove-host");
    // Version lives in the status bar now — de-duplicated out of the rail.
    expect(footer).not.toHaveTextContent("v0.1.0");
  });
});
