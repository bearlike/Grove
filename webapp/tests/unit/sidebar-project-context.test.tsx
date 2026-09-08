import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  ProjectContextPicker,
  projectContextOf,
  projectLaunchHref,
  scopeFleetRows,
} from "@/components/grove/fleet/project-context";
import { fleetRows } from "@/components/grove/fleet/filter";
import { project, snapshot, workspace } from "@/tests/fixtures/fleet";

const nestedProjects = () => [
  {
    ...project("Grove", "/repos/grove", [workspace({ id: "root-workspace" })]),
    cwd: "/repos/grove",
  },
  {
    ...project("Grove", "/repos/grove", [workspace({ id: "web-workspace" })]),
    cwd: "/repos/grove/web",
  },
] as const;

describe("project sidebar context", () => {
  it("scopes same-root nested projects by the snapshot group membership", () => {
    const projects = nestedProjects();
    const rows = fleetRows(snapshot(projects));
    const context = projectContextOf("/repos/grove/web", projects);

    expect(context.kind).toBe("selected");
    expect(scopeFleetRows(rows, context).map((row) => row.workspace.state.id)).toEqual([
      "web-workspace",
    ]);
  });

  it("keeps a vanished explicit selection empty instead of widening to all projects", () => {
    const projects = nestedProjects();
    const rows = fleetRows(snapshot(projects));
    const context = projectContextOf("/repos/removed", projects);

    expect(context).toEqual({ kind: "missing", cwd: "/repos/removed" });
    expect(scopeFleetRows(rows, context)).toEqual([]);
  });

  it("keeps all projects distinct from an unavailable selection", () => {
    const projects = nestedProjects();
    const rows = fleetRows(snapshot(projects));

    expect(scopeFleetRows(rows, projectContextOf(null, projects))).toHaveLength(2);
    expect(projectLaunchHref(projectContextOf(null, projects))).toBe("/");
  });

  it("carries a selected nested project to the existing launch selection path", () => {
    const context = projectContextOf("/repos/grove/web", nestedProjects());

    expect(projectLaunchHref(context)).toBe("/?project=%2Frepos%2Fgrove%2Fweb");
  });

  it("labels a context control separately from its visible value", () => {
    const projects = nestedProjects();
    const html = renderToStaticMarkup(
      <ProjectContextPicker
        projects={projects}
        context={projectContextOf("/repos/grove/web", projects)}
        onSelect={() => undefined}
      />,
    );

    expect(html).toContain('data-testid="rail-project-context"');
    expect(html).toContain('role="combobox"');
    expect(html).toContain('aria-label="Project context"');
    expect(html).toContain('data-variant="outline"');
    // Pinned against the action row's own source, not a second literal. `h-6`
    // matters: without it the vendored `sm` wins and only the floor reads 24.
    expect(html).toContain("h-6");
    expect(html).toContain("min-h-[24px]");
    expect(readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8"))
      .toContain("h-6 min-h-[24px]");
    expect(html).toContain("lucide-chevrons-up-down");
    expect(html).toContain("Grove — web");
  });

  it("has one context controller above both sidebar copies", () => {
    const shell = readFileSync("components/grove/shell/app-shell.tsx", "utf8");
    const sidebar = readFileSync("components/grove/shell/app-sidebar.tsx", "utf8");
    const tree = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");

    expect(shell).toContain("const project = useProjectContext(stream.snapshot?.projects ?? []);");
    expect(shell.match(/<AppSidebar\b[\s\S]*?project=\{project\}[\s\S]*?\/>/g)).toHaveLength(2);
    expect(sidebar).toContain("project: ProjectContextController;");
    expect(sidebar).toMatch(/<FleetTree\b[^>]*project=\{project\}[^>]*\/>/);
    expect(tree).toContain("project: ProjectContextController;");
    expect(tree).not.toContain("useProjectContext(");
  });
});
