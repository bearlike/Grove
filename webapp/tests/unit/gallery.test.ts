import { describe, expect, it } from "vitest";

import { ACCOUNT_ITEMS, RAIL_ITEMS, sectionFor } from "@/components/grove/shell/nav";
import {
  NO_GALLERY_FILTER,
  filterGallery,
  galleryCardCaption,
  galleryCardTitle,
  galleryCountLabel,
  galleryExportFilename,
  galleryExportMessage,
  galleryExportToken,
  galleryOpenTarget,
  galleryPageDocument,
  galleryPages,
  galleryProjects,
  galleryViewerUrl,
} from "@/lib/grove/adapters";
import type { GalleryItemView } from "@/lib/grove/api";

function item(overrides: Partial<GalleryItemView> = {}): GalleryItemView {
  return {
    id: "a".repeat(64),
    name: "board.drawio",
    relative_path: "docs/board.drawio",
    repo_root: "/repos/grove",
    repo_name: "Grove",
    worktree_path: "/repos/grove",
    size_bytes: 1234,
    modified_at: "2026-09-13T20:00:00Z",
    digest: "b".repeat(64),
    pages: 3,
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    workspace_live: false,
    session_id: null,
    session_kind: null,
    session_cwd: null,
    session_title: null,
    session_live: false,
    preview_ready: false,
    ...overrides,
  };
}

describe("the gallery is an account-menu destination", () => {
  it("sits beside All Sessions, off the rail, and titles its own routes", () => {
    expect(ACCOUNT_ITEMS.some((entry) => entry.href === "/gallery")).toBe(true);
    expect(RAIL_ITEMS.some((entry) => entry.href === "/gallery")).toBe(false);
    expect(sectionFor("/gallery").label).toBe("Gallery");
  });
});

describe("card lines", () => {
  it("titles by session, then workspace, then short id, then file", () => {
    expect(galleryCardTitle(item({ session_title: "sidebar review", workspace_title: "ws" }))).toBe(
      "sidebar review",
    );
    expect(galleryCardTitle(item({ workspace_title: "ws" }))).toBe("ws");
    expect(galleryCardTitle(item({ session_id: "cf968cda-1234" }))).toBe("cf968cda");
    expect(galleryCardTitle(item())).toBe("board.drawio");
  });

  it("captions `N pages • Project`, dropping an unparsed page count", () => {
    expect(galleryCardCaption(item())).toBe("3 pages  •  Grove");
    expect(galleryCardCaption(item({ pages: 1 }))).toBe("1 page  •  Grove");
    expect(galleryCardCaption(item({ pages: null }))).toBe("Grove");
  });
});

describe("the last menu item follows liveness", () => {
  it("opens the workspace when it is live", () => {
    const target = galleryOpenTarget(
      item({ workspace_id: "w1", workspace_live: true, session_id: "s1", session_kind: "claude_code", session_cwd: "/x" }),
    );
    expect(target).toEqual({ kind: "workspace", href: "/w/w1" });
  });

  it("opens the session when the workspace is not live but a transcript exists", () => {
    const target = galleryOpenTarget(
      item({ workspace_id: "w1", workspace_live: false, session_id: "s1", session_kind: "claude_code", session_cwd: "/x y" }),
    );
    expect(target?.kind).toBe("session");
    expect(target?.href).toBe("/sessions/s1?kind=claude_code&cwd=%2Fx+y");
  });

  it("offers nothing for a file with neither", () => {
    expect(galleryOpenTarget(item())).toBeNull();
  });
});

describe("narrowing and ordering", () => {
  const rows = [
    item({ id: "1".repeat(64), name: "zeta.drawio", modified_at: "2026-09-13T10:00:00Z", session_title: "zeta" }),
    item({ id: "2".repeat(64), name: "alpha.drawio", modified_at: "2026-09-13T12:00:00Z", repo_root: "/repos/other", repo_name: "Assistant", session_title: "alpha" }),
    item({ id: "3".repeat(64), name: "mid.drawio", modified_at: "2026-09-13T11:00:00Z", session_title: "mid" }),
  ];

  it("defaults to newest first", () => {
    expect(filterGallery(rows, NO_GALLERY_FILTER).map((r) => r.name)).toEqual([
      "alpha.drawio",
      "mid.drawio",
      "zeta.drawio",
    ]);
  });

  it("sorts by name and by project, newest breaking ties", () => {
    expect(filterGallery(rows, { ...NO_GALLERY_FILTER, sort: "name" }).map((r) => r.name)).toEqual([
      "alpha.drawio",
      "mid.drawio",
      "zeta.drawio",
    ]);
    expect(filterGallery(rows, { ...NO_GALLERY_FILTER, sort: "project" }).map((r) => r.repo_name)).toEqual([
      "Assistant",
      "Grove",
      "Grove",
    ]);
  });

  it("narrows by project root and by what the card shows", () => {
    expect(filterGallery(rows, { ...NO_GALLERY_FILTER, project: "/repos/other" }).map((r) => r.name)).toEqual([
      "alpha.drawio",
    ]);
    expect(filterGallery(rows, { ...NO_GALLERY_FILTER, query: "ZETA" }).map((r) => r.name)).toEqual([
      "zeta.drawio",
    ]);
    expect(filterGallery(rows, { ...NO_GALLERY_FILTER, query: "nothing" })).toEqual([]);
  });

  it("lists distinct projects alphabetically and states both count bounds", () => {
    expect(galleryProjects(rows)).toEqual([
      { root: "/repos/other", name: "Assistant" },
      { root: "/repos/grove", name: "Grove" },
    ]);
    expect(galleryCountLabel(3, 3)).toBe("3 diagrams");
    expect(galleryCountLabel(1, 3)).toBe("1 of 3 diagrams");
    expect(galleryCountLabel(1, 1)).toBe("1 diagram");
  });
});

describe("the viewer and export protocol", () => {
  it("mounts the chromeless viewer with the JSON protocol", () => {
    const url = new URL(galleryViewerUrl("https://embed.diagrams.net/"));
    expect(url.searchParams.get("proto")).toBe("json");
    expect(url.searchParams.get("chrome")).toBe("0");
    expect(url.searchParams.get("configure")).toBe("1");
  });

  it("asks for a PNG at the scale given, carries its token, and never an xml", () => {
    const frame = JSON.parse(galleryExportMessage("tok", { scale: 2, pageId: "p1" })) as Record<string, unknown>;
    expect(frame).toMatchObject({ action: "export", format: "png", scale: 2, pageId: "p1", groveExport: "tok" });
    expect(frame).not.toHaveProperty("xml");
    expect(galleryExportToken(frame)).toBe("tok");
    expect(galleryExportToken({ other: 1 })).toBeNull();
    expect(galleryExportToken(null)).toBeNull();
  });

  it("names the export after the diagram, joining a page name when given", () => {
    expect(galleryExportFilename("board.drawio")).toBe("board.png");
    expect(galleryExportFilename("board.drawio", "01 Before / after")).toBe("board-01-Before-after.png");
    expect(galleryExportFilename(".drawio")).toBe("diagram.png");
  });

  it("reads the pages off an mxfile in order", () => {
    const xml =
      '<mxfile><diagram id="a" name="One"><mxGraphModel/></diagram>' +
      "<diagram name='Two' id='b'><mxGraphModel/></diagram><diagram><mxGraphModel/></diagram></mxfile>";
    expect(galleryPages(xml)).toEqual([
      { id: "a", name: "One" },
      { id: "b", name: "Two" },
      { id: "2", name: "Page 3" },
    ]);
  });
});

describe("one page at a time", () => {
  const xml =
    '<mxfile host="x"><diagram id="a" name="One"><mxGraphModel><root/></mxGraphModel></diagram>' +
    '<diagram id="b" name="Two"><mxGraphModel><root/></mxGraphModel></diagram></mxfile>';

  it("reduces the document to the page asked for and keeps the mxfile shell", () => {
    const only = galleryPageDocument(xml, "b");
    expect(only).toContain('<mxfile host="x">');
    expect(only).toContain('id="b"');
    expect(only).not.toContain('id="a"');
    expect(galleryPages(only ?? "")).toEqual([{ id: "b", name: "Two" }]);
  });

  it("refuses a page the file does not hold", () => {
    expect(galleryPageDocument(xml, "zzz")).toBeNull();
    expect(galleryPageDocument("<nope/>", "a")).toBeNull();
  });
});
