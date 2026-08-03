import { describe, it, expect, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SidebarFilter } from "@/components/layout/sidebar-filter";
import { useUiStore } from "@/lib/grove/ui-store";

// The rail's compact filter — the SINGLE organizing instrument for the rail's
// flat list. Drives the real ui-store (a singleton, reset per test) and
// pins the seams the rail application + e2e depend on: `sidebar-filter-trigger`,
// `filter-state-<s>`, `filter-attention-only`, `filter-project-<root>`,
// `filter-show-unmapped`, `filter-clear`, and the active-filter dot.

const PROJECTS = [
  { repo_root: "/repos/Grove", repo_name: "Grove" },
  { repo_root: "/repos/website", repo_name: "website" },
];

describe("SidebarFilter", () => {
  beforeEach(() => {
    useUiStore.setState({
      hiddenStates: [],
      hiddenProjects: [],
      attentionOnly: false,
      showUnmapped: false,
      query: "",
      scopeRepo: null,
    });
  });

  it("hiding a state writes hiddenStates and marks the trigger active", async () => {
    const user = userEvent.setup();
    render(<SidebarFilter projects={PROJECTS} />);
    // Inactive → no dot on the trigger.
    expect(screen.queryByTestId("sidebar-filter-active")).toBeNull();

    await user.click(screen.getByTestId("sidebar-filter-trigger"));
    // A shown state is checked; unchecking it hides that state.
    await user.click(screen.getByTestId("filter-state-idle"));

    expect(useUiStore.getState().hiddenStates).toContain("idle");
    expect(screen.getByTestId("sidebar-filter-active")).toBeInTheDocument();
  });

  it("hides a project (a checked row = shown) and marks the trigger active", async () => {
    const user = userEvent.setup();
    render(<SidebarFilter projects={PROJECTS} />);
    await user.click(screen.getByTestId("sidebar-filter-trigger"));
    await user.click(screen.getByTestId("filter-project-/repos/website"));
    expect(useUiStore.getState().hiddenProjects).toEqual(["/repos/website"]);
    expect(screen.getByTestId("sidebar-filter-active")).toBeInTheDocument();
  });

  it("toggles show-unmapped and counts it toward the active-filter dot", async () => {
    const user = userEvent.setup();
    render(<SidebarFilter projects={PROJECTS} />);
    await user.click(screen.getByTestId("sidebar-filter-trigger"));
    await user.click(screen.getByTestId("filter-show-unmapped"));
    expect(useUiStore.getState().showUnmapped).toBe(true);
    expect(screen.getByTestId("sidebar-filter-active")).toBeInTheDocument();
  });

  it("Clear resets every filter — states, projects, attention, showUnmapped", async () => {
    const user = userEvent.setup();
    useUiStore.setState({
      hiddenStates: ["idle"],
      hiddenProjects: ["/repos/Grove"],
      attentionOnly: true,
      showUnmapped: true,
    });
    render(<SidebarFilter projects={PROJECTS} />);

    await user.click(screen.getByTestId("sidebar-filter-trigger"));
    await user.click(screen.getByTestId("filter-clear"));
    const s = useUiStore.getState();
    expect(s.hiddenStates).toEqual([]);
    expect(s.hiddenProjects).toEqual([]);
    expect(s.attentionOnly).toBe(false);
    expect(s.showUnmapped).toBe(false);
  });

  it("omits the Projects section when no projects are passed", async () => {
    const user = userEvent.setup();
    render(<SidebarFilter projects={[]} />);
    await user.click(screen.getByTestId("sidebar-filter-trigger"));
    expect(screen.queryByTestId("filter-project-/repos/Grove")).toBeNull();
    // The show-unmapped escape hatch is always present.
    expect(screen.getByTestId("filter-show-unmapped")).toBeInTheDocument();
  });
});
