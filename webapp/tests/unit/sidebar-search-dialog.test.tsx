import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const shell = readFileSync("components/grove/shell/app-shell.tsx", "utf8");
const sidebar = readFileSync("components/grove/shell/app-sidebar.tsx", "utf8");
const tree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");
const palette = readFileSync("components/grove/fleet/fleet-palette.tsx", "utf8");

describe("sidebar workspace search", () => {
  it("keeps the one search controller and its ⌘K listener at the shell", () => {
    expect(shell).toContain("useFleetSearch");
    expect(shell).toContain('window.addEventListener("keydown", onKeyDown)');
    expect(shell).toContain("<FleetOverlays\n");
    expect(shell).toContain("snapshot={stream.snapshot}");
    expect(palette).not.toContain('window.addEventListener("keydown", onKeyDown)');
    expect(palette).toContain("open={search.open}");
    expect(palette).toContain("onOpenChange={onOpenChange}");
  });

  it("shares that controller with both rail mounts and opens search only after closing the sheet", () => {
    expect(shell).toContain("onOpenSearch={openSearch}");
    expect(shell).toContain("search={search}");
    expect(shell).toContain("setMobileOpen(false)");
    expect(shell).toContain("setSearchAfterSheet(true)");
  });

  it("replaces persistent search with a brand trigger and keeps filtering controls on the action row", () => {
    expect(sidebar).toContain("SearchIcon");
    expect(sidebar).toContain("onOpenSearch");
    expect(sidebar).toContain("searchTooltip");
    expect(sidebar).not.toContain("MarqueePauseButton");
    expect(tree).not.toContain("ThreadListSearch");
    expect(tree).toContain("FleetFilterMenu");
    expect(tree).toContain("MarqueePauseButton");
    expect(tree).not.toContain("const [filter, setFilter]");
  });

  it("uses one native sidebar filter menu inside the dialog and filters its recent results", () => {
    expect(palette).toContain('<FleetFilterMenu\n');
    expect(palette).toContain('variant="sidebar"');
    expect(palette).toContain("shouldFilter={false}");
    expect(palette).toContain("search.visibleRows.map");
    expect(palette).toContain("value={search.filter.query}");
  });

  it("opens after a mobile sheet closes rather than on every closed-sheet render", () => {
    expect(shell).toContain("if (mobileOpen || !searchAfterSheet) return;");
    expect(shell).not.toContain("!setSearchAfterSheet");
  });

  it("returns focus to the recorded opener and reserves mobile sheet close-button space", () => {
    expect(shell).toContain("const searchOpener = useRef<HTMLElement | null>(null);");
    expect(shell).toContain("document.querySelector<HTMLButtonElement>(\"[data-testid=\\\"shell-sidebar-sheet\\\"]\")");
    expect(shell).not.toContain('className="flex min-h-0 flex-1 flex-col pr-10"');
    expect(shell).toContain("showCloseButton={false}");
    expect(shell).toContain("<SheetClose asChild>");
    expect(sidebar).toContain("{headerAction}");
    expect(palette).toContain("onCloseAutoFocus");
    expect(palette).toContain("onCloseAutoFocus();");
  });

  it("keeps collapsed action controls inside its rail and lets the input fill", () => {
    expect(sidebar).toContain("{collapsed ? null : (");
    expect(sidebar).toContain('collapsed && "mx-auto"');
    expect(tree).toContain("{collapsed ? null : (");
    expect(tree).toContain("<FleetFilterMenu");
    expect(tree).toContain("<MarqueePauseButton");
    expect(palette).toContain('className="min-w-0 flex-1"');
  });
});
