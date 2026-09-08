import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { sortedFleetRows, isInactive } from "@/components/grove/fleet/filter";
import { WorkspaceCard } from "@/components/grove/fleet/workspace-card";
import { TooltipProvider } from "@/components/ui/tooltip";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

describe("dense workspace scan contracts", () => {
  it("keeps a freshly observed offline workspace below a live idle session", () => {
    const offline = workspace({ id: "offline", lastEventAt: "2026-09-06T12:00:00Z" });
    offline.state.status = "offline";
    const live = workspace({ id: "live", state: "idle", lastEventAt: "2026-08-01T12:00:00Z" });
    expect(isInactive(live)).toBe(false);
    expect(sortedFleetRows(snapshot([project("Sample", "/repos/sample", [offline, live])])).map(r => r.workspace.state.id)).toEqual(["live", "offline"]);
  });
  it("has no reserved blank status height or primary-colored title", () => {
    const html = renderToStaticMarkup(<TooltipProvider><WorkspaceCard workspace={workspace({ id: "sample" })} repoName="Sample" /></TooltipProvider>);
    expect(html).not.toContain("min-h-12");
    expect(html).not.toContain('data-variant="link"');
    expect(html).not.toContain("bg-warning");
    expect(html).toContain("h-5");
  });
  it("marks the current destination with an edge and a marker, never a fill", () => {
    const tree = readFileSync(new URL("../../components/grove/fleet/fleet-tree.tsx", import.meta.url), "utf8");
    const row = tree.slice(tree.indexOf("function WorkspaceRow("));
    // Selection belongs to the navigational link, not a button wrapper: a
    // raised card cannot be a nested target when its options control is a sibling.
    expect(row).toContain("<CardShell");
    expect(row).toContain("<Link");
    expect(row).not.toContain('variant="secondary"');
    expect(row).toContain("active ? ROW_SELECTED : cn(ROW_RESTING,");
    expect(row).toContain('data-testid="fleet-row-marker"');
    expect(row).toContain('aria-current={active ? "page" : undefined}');
    expect(row).not.toContain("opacity-80");
  });
  it("gives a project heading more room above it than below, without a separator", () => {
    const tree = readFileSync(new URL("../../components/grove/fleet/fleet-tree.tsx", import.meta.url), "utf8");
    // A heading belongs to the group BELOW it. The old stack put 34.6px above
    // the label and 14.4px below (a separator's `my-1.5` between two `gap-3`
    // section gaps), so every project name read as a footer for the group it
    // followed. The rule was redundant once `--border` became visible.
    expect(tree).not.toContain("<Separator");
    expect(tree).toContain('index > 0 ? "pt-2" : "pt-0.5"');
    expect(tree).toContain("pb-1.5");
  });
  it("defaults the rail to grouped and uses native keyboard menus", () => {
    const tree = readFileSync(new URL("../../components/grove/fleet/fleet-tree.tsx", import.meta.url), "utf8");
    const menu = readFileSync(new URL("../../components/grove/fleet/fleet-filter.tsx", import.meta.url), "utf8");
    const shell = readFileSync(new URL("../../components/grove/shell/app-shell.tsx", import.meta.url), "utf8");
    expect(shell).toContain('useState<FleetFilter>({ ...NO_FILTER, groupBy: "project" })');
    expect(tree).toContain("search.filter.groupBy");
    expect(menu).toContain("DropdownMenuCheckboxItem");
    expect(menu).not.toContain("PopoverContent");
  });
});
