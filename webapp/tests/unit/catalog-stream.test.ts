import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const read = (path: string): string => readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

const sse = read("lib/grove/api/sse.ts");
const stream = read("lib/grove/hooks/stream.tsx");
const queries = read("lib/grove/hooks/queries.ts");
const gallery = read("lib/grove/hooks/gallery.ts");

describe("catalog_changed stream migration", () => {
  it("admits the catalog frame and invalidates only catalog and gallery roots", () => {
    expect(sse).toContain('"catalog_changed"');
    const start = stream.indexOf('case "catalog_changed":');
    const end = stream.indexOf('case "refetch":', start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const action = stream.slice(start, end);
    expect(action).toContain("groveKeys.catalog()");
    expect(action).toContain("groveKeys.gallery()");
    expect(action).not.toContain("groveKeys.activity");
    expect(action).not.toContain("groveKeys.workspace(");
  });

  it("uses catalog_changed as the catalog and gallery polling backstop", () => {
    expect(queries).toMatch(/useSessionCatalog[\s\S]*backstopInterval\(connected, POLL_MS\.catalog\)/);
    expect(gallery).toMatch(/useGallery[\s\S]*backstopInterval\(connected, POLL_MS\.catalog\)/);
  });
});

describe("session_activity query migration", () => {
  it("writes the delivered row into the per-workspace activity cache", () => {
    const start = stream.indexOf('case "apply":');
    const end = stream.indexOf('case "drop":', start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const apply = stream.slice(start, end);
    expect(apply).toContain("groveKeys.workspaceActivity(action.workspace.state.id)");
    expect(apply).toContain("action.workspace");
  });

  it("invalidates the stream-covered workspace reads", () => {
    const start = stream.indexOf("function refreshWorkspaceQueries(");
    const end = stream.indexOf("function refreshPeekFromStream(", start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    const refresh = stream.slice(start, end);
    expect(refresh).toContain("groveKeys.provision(workspaceId)");
    expect(refresh).toContain("groveKeys.sessions(workspaceId)");
    expect(refresh).toContain("groveKeys.diagram(workspaceId)");

    expect(queries).toMatch(/useProvisionProgress[\s\S]*backstopInterval\(connected, POLL_MS\.provision\)/);
    expect(queries).toMatch(/useWorkspaceSessions[\s\S]*backstopInterval\(connected, POLL_MS\.sessions\)/);
  });
});

/**
 * `workspace_source_changed` is the engine's answer to a read whose content
 * moves without any counter moving — a file edited while already dirty. The
 * defect it closes is silent: `dirty_files` is a SET, so re-editing a staged
 * file produced no `session_activity` delta and the interval was the only
 * freshness the Files tab had.
 */
describe("workspace_source_changed query migration", () => {
  it("admits the source frame and invalidates the whole workspace subtree", () => {
    expect(sse).toContain('"workspace_source_changed"');
    const start = stream.indexOf('case "source_changed":');
    const end = stream.indexOf('case "catalog_changed":', start);
    expect(start).toBeGreaterThan(0);
    expect(end).toBeGreaterThan(start);
    // `groveKeys.workspace(id)` is the PREFIX of diff/diagram/peek, so one
    // invalidation reaches every per-workspace read. Naming diff explicitly
    // here would pin an implementation the key hierarchy already guarantees.
    expect(stream.slice(start, end)).toContain("groveKeys.workspace(action.workspaceId)");
  });

  it("gates the diff poll on the stream and keeps the diagram conflict detector", () => {
    expect(queries).toMatch(/useWorkspaceDiff[\s\S]*backstopInterval\(connected, POLL_MS\.diff\)/);
    // NOT gated: this interval detects an outside write under an open editor,
    // and a dropped stream must not be why a draft is silently overwritten.
    expect(queries).toMatch(/useWorkspaceDiagram[\s\S]*watch \? POLL_MS\.diagram : false/);
  });
});
