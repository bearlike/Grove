import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { workspaceLaunchHref } from "@/components/grove/fleet/project-context";
import { GroveProtocolError, isWorkspaceGone } from "@/lib/grove/api";
import { project, workspace } from "@/tests/fixtures/fleet";

/**
 * Killing a workspace lands on a NEW workspace in the same project, never on
 * "Couldn't load this workspace" and never on the unscoped landing page.
 */
describe("leaving a killed workspace", () => {
  const projects = [
    { ...project("Grove", "/repos/grove", [workspace({ id: "root-ws" })]), cwd: "/repos/grove" },
    { ...project("Grove", "/repos/grove", [workspace({ id: "web-ws" })]), cwd: "/repos/grove/web" },
  ];

  it("seeds the landing page with the project that files the workspace", () => {
    // Two configured projects share one repo root; the nested one must win for
    // its own workspace, or the new workspace starts in the wrong directory.
    expect(workspaceLaunchHref(projects, "web-ws")).toBe("/?project=%2Frepos%2Fgrove%2Fweb");
    expect(workspaceLaunchHref(projects, "root-ws")).toBe("/?project=%2Frepos%2Fgrove");
  });

  it("says nothing for a workspace no project files, so the caller keeps its last answer", () => {
    expect(workspaceLaunchHref(projects, "gone")).toBeNull();
  });

  it("treats only the daemon's workspace_not_found as gone", () => {
    expect(isWorkspaceGone(new GroveProtocolError("workspace_not_found", "no workspace", 404))).toBe(true);
    // Another 404 (a missing session, a missing diagram) is not the workspace.
    expect(isWorkspaceGone(new GroveProtocolError("agent_session_not_found", "x", 404))).toBe(false);
    expect(isWorkspaceGone(new Error("network"))).toBe(false);
  });

  it("routes every kill path on the workspace page through the same exit", () => {
    const page = readFileSync("components/grove/workspace/index.tsx", "utf8");
    expect(page).toContain("onKilled: leave,");
    expect(page).toContain("if (gone) leave();");
    expect(page).not.toContain('router.push("/")');
    const rail = readFileSync("components/grove/fleet/fleet-tree.tsx", "utf8");
    expect(rail).toContain("router.replace(launchHref)");
    expect(rail).not.toContain('router.push("/")');
  });
});
