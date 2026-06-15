import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { ThemeProvider } from "next-themes";
import { WorkspaceGrid } from "@/components/workspace/workspace-grid";
import { useUiStore } from "@/lib/grove/ui-store";
import { workspace } from "@/tests/_helpers/activity-fixtures";
import type { DashboardSnapshotView, WorkspaceActivityView } from "@/lib/grove/types";

// The grid (issue #96 deliverables C/D): repo-grouped sections, attention-first
// within a section, every view-intent read straight off the Zustand store. The
// ONLY public prop is the server `snapshot`.

function inRepo(w: WorkspaceActivityView, repo_root: string): WorkspaceActivityView {
  return { ...w, state: { ...w.state, repo_root } };
}

/** Two repos: Grove (working g1 + idle g2) and website (blocked s1, wants the human). */
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

function r(snapshot: DashboardSnapshotView) {
  return render(
    <ThemeProvider attribute="class" defaultTheme="dark">
      <WorkspaceGrid snapshot={snapshot} />
    </ThemeProvider>,
  );
}

beforeEach(() => {
  // The store is a module-level singleton — reset the view slices each test.
  useUiStore.setState({
    query: "",
    scopeRepo: null,
    hiddenStates: [],
    attentionOnly: false,
    liveId: null,
  });
});

describe("WorkspaceGrid", () => {
  it("groups workspaces into one section per repo, each with a header + count", () => {
    r(buildSnapshot());
    const sections = screen.getAllByTestId("project-section");
    expect(sections.map((s) => s.getAttribute("data-repo"))).toEqual([
      "/repos/Grove",
      "/repos/website",
    ]);
    const grove = sections[0];
    const header = within(grove).getByTestId("project-section-header");
    expect(header).toHaveTextContent("Grove");
    expect(header).toHaveTextContent("2");
    expect(within(grove).getAllByTestId("workspace-card")).toHaveLength(2);
  });

  it("orders cards attention-first within a section", () => {
    // website's single card is blocked → attention; but ordering is per-section.
    // Put a working + blocked in the SAME repo and assert blocked floats up.
    const both = [
      inRepo(workspace("w-work", "working"), "/repos/Grove"),
      inRepo(workspace("w-block", "blocked"), "/repos/Grove"),
    ];
    r({
      projects: [{ repo_root: "/repos/Grove", repo_name: "Grove", workspaces: both }],
      generated_at: "2026-06-01T00:00:00Z",
      total_workspaces: 2,
      needs_attention: 1,
    });
    const cards = screen.getAllByTestId("workspace-card");
    expect(cards[0]).toHaveAttribute("data-agent-state", "blocked");
    expect(cards[1]).toHaveAttribute("data-agent-state", "working");
  });

  it("renders only the scoped repo's section when scopeRepo is set", () => {
    useUiStore.setState({ scopeRepo: "/repos/website" });
    r(buildSnapshot());
    const sections = screen.getAllByTestId("project-section");
    expect(sections).toHaveLength(1);
    expect(sections[0].getAttribute("data-repo")).toBe("/repos/website");
  });

  it("hides a state and drops a section emptied by it", () => {
    // Hiding working+idle empties Grove; website (blocked) survives.
    useUiStore.setState({ hiddenStates: ["working", "idle"] });
    r(buildSnapshot());
    const sections = screen.getAllByTestId("project-section");
    expect(sections.map((s) => s.getAttribute("data-repo"))).toEqual(["/repos/website"]);
  });

  it("attentionOnly keeps only workspaces that want the human", () => {
    useUiStore.setState({ attentionOnly: true });
    r(buildSnapshot());
    expect(screen.getAllByTestId("workspace-card")).toHaveLength(1);
    expect(screen.getByTestId("workspace-card")).toHaveAttribute("data-agent-state", "blocked");
  });

  it("filters by query over title", () => {
    useUiStore.setState({ query: "g1" });
    r(buildSnapshot());
    const cards = screen.getAllByTestId("workspace-card");
    expect(cards).toHaveLength(1);
    expect(within(cards[0]).getByRole("link", { name: "t-g1" })).toBeInTheDocument();
  });

  it("shows an empty state when nothing matches but workspaces exist", () => {
    useUiStore.setState({ query: "no-such-workspace" });
    r(buildSnapshot());
    expect(screen.queryByTestId("project-section")).toBeNull();
    expect(screen.getByTestId("workspace-grid")).toHaveTextContent(/widen it/i);
  });

  it("shows the zero-workspaces empty state", () => {
    r({
      projects: [],
      generated_at: "2026-06-01T00:00:00Z",
      total_workspaces: 0,
      needs_attention: 0,
    });
    expect(screen.getByTestId("workspace-grid")).toHaveTextContent(/No workspaces across any project/i);
  });
});
