import { describe, expect, it } from "vitest";

import {
  absolutePathError,
  adoptActionLabel,
  adoptRequests,
  cloneRequest,
  cloneUrlError,
  createRequest,
  destinationPreview,
  filterCandidates,
  folderFromUrl,
  projectNameError,
  selectedPaths,
  selectionSummary,
} from "@/lib/grove/adapters/add-project";
import type { DashboardSnapshotView, ProjectCandidateView } from "@/lib/grove/api";
import { withProject } from "@/lib/grove/adapters/activity";

const ROOT = "/home/demo/Projects";

const widgets: ProjectCandidateView = { name: "widgets", path: `${ROOT}/widgets`, remote: "acme/widgets" };
const gadgets: ProjectCandidateView = { name: "gadgets", path: `${ROOT}/gadgets`, remote: null };
const notes: ProjectCandidateView = { name: "notes", path: `${ROOT}/notes`, remote: "acme/notes" };
const ALL = [widgets, gadgets, notes];

describe("projectNameError", () => {
  it("accepts a single folder name", () => {
    expect(projectNameError("weather-bot")).toBeNull();
    expect(projectNameError("_scratch.v2")).toBeNull();
  });

  it.each([
    ["", "Enter a name."],
    ["a/b", "Use one folder name, not a path."],
    ["a\\b", "Use one folder name, not a path."],
    ["x".repeat(101), "Use 100 characters or fewer."],
  ])("refuses %j with its own reason", (name, reason) => {
    expect(projectNameError(name)).toBe(reason);
  });

  it("refuses a flag-shaped or dot-led name, which git or the shell would misread", () => {
    expect(projectNameError("-rf")).toMatch(/^Start with/);
    expect(projectNameError(".hidden")).toMatch(/^Start with/);
  });
});

describe("cloneUrlError", () => {
  it("accepts ssh and https URLs", () => {
    expect(cloneUrlError("git@git.example.com:acme/widgets.git")).toBeNull();
    expect(cloneUrlError("https://git.example.com/acme/widgets")).toBeNull();
  });

  it("refuses empty, spaced and flag-shaped input", () => {
    expect(cloneUrlError("   ")).toBe("Enter a repository URL.");
    expect(cloneUrlError("https://x/a b")).toBe("A URL cannot contain spaces.");
    expect(cloneUrlError("--upload-pack=evil")).toBe("A URL cannot start with '-'.");
  });
});

describe("absolutePathError", () => {
  it("is silent while the field is empty, so an unused alternative never refuses", () => {
    expect(absolutePathError("")).toBeNull();
    expect(absolutePathError("  ")).toBeNull();
  });

  it("accepts absolute and home-relative paths and refuses a relative one", () => {
    expect(absolutePathError("/srv/repo")).toBeNull();
    expect(absolutePathError("~/repo")).toBeNull();
    expect(absolutePathError("repo")).toMatch(/absolute path/);
  });
});

describe("folderFromUrl", () => {
  it.each([
    ["git@git.example.com:acme/widgets.git", "widgets"],
    ["https://git.example.com/acme/widgets", "widgets"],
    ["https://git.example.com/acme/widgets/", "widgets"],
    ["https://git.example.com/acme/widgets/.git", "widgets"],
    ["", ""],
  ])("%j → %j", (url, folder) => {
    expect(folderFromUrl(url)).toBe(folder);
  });

  it("derives nothing rather than a name the daemon would refuse", () => {
    expect(folderFromUrl("https://git.example.com/acme/-bad")).toBe("");
  });
});

describe("destinationPreview", () => {
  it("joins the parent and folder, dropping a trailing slash", () => {
    expect(destinationPreview(`${ROOT}/`, "widgets")).toBe(`${ROOT}/widgets`);
  });

  it("is null while either half is missing, so no half-path is ever previewed", () => {
    expect(destinationPreview("", "widgets")).toBeNull();
    expect(destinationPreview(ROOT, "")).toBeNull();
  });
});

describe("requests", () => {
  it("omits an untouched location so the daemon resolves its own default", () => {
    expect(createRequest("bot", ROOT, ROOT)).toEqual({ kind: "create", name: "bot" });
    expect(createRequest("bot", "/srv", ROOT)).toEqual({ kind: "create", name: "bot", location: "/srv" });
  });

  it("omits a folder name equal to the URL-derived one, and sends an edited one", () => {
    const url = "git@git.example.com:acme/widgets.git";
    expect(cloneRequest(url, "widgets", ROOT, ROOT)).toEqual({ kind: "clone", url });
    expect(cloneRequest(` ${url} `, "w2", "/srv", ROOT)).toEqual({
      kind: "clone",
      url,
      name: "w2",
      location: "/srv",
    });
  });

  it("makes one adopt request per path, in order", () => {
    expect(adoptRequests(["/a", "/b"])).toEqual([
      { kind: "adopt", path: "/a" },
      { kind: "adopt", path: "/b" },
    ]);
  });
});

describe("filterCandidates", () => {
  it("matches name, path or remote, case-insensitively", () => {
    expect(filterCandidates(ALL, "WIDG").map((c) => c.name)).toEqual(["widgets"]);
    expect(filterCandidates(ALL, "acme/notes").map((c) => c.name)).toEqual(["notes"]);
    expect(filterCandidates(ALL, "/gadgets").map((c) => c.name)).toEqual(["gadgets"]);
  });

  it("returns every candidate for a blank query", () => {
    expect(filterCandidates(ALL, "  ")).toHaveLength(3);
  });

  it("does not crash on a candidate with no remote", () => {
    expect(filterCandidates([gadgets], "acme")).toEqual([]);
  });
});

describe("selectionSummary", () => {
  it("counts every match, not just the rows on screen", () => {
    expect(selectionSummary(ALL, ALL, new Set())).toEqual({ matching: 3, selected: 0, hidden: 0 });
  });

  it("keeps a selection the filter hides, and says it is hidden", () => {
    const selected = new Set([widgets.path, notes.path]);
    const visible = filterCandidates(ALL, "widgets");
    expect(selectionSummary(ALL, visible, selected)).toEqual({ matching: 1, selected: 2, hidden: 1 });
  });

  it("drops a selected path that is no longer a candidate, so Add N never names a phantom", () => {
    const selected = new Set([widgets.path, `${ROOT}/gone`]);
    expect(selectionSummary(ALL, ALL, selected)).toEqual({ matching: 3, selected: 1, hidden: 0 });
  });
});

describe("selectedPaths and adoptActionLabel", () => {
  it("returns live selected paths in candidate order", () => {
    expect(selectedPaths(ALL, new Set([notes.path, widgets.path, "/gone"]))).toEqual([
      widgets.path,
      notes.path,
    ]);
  });

  it("names the effect and its count", () => {
    expect(adoptActionLabel(1)).toBe("Add 1 project");
    expect(adoptActionLabel(2)).toBe("Add 2 projects");
    expect(adoptActionLabel(0)).toBe("Add 0 projects");
  });
});

describe("withProject", () => {
  const base = {
    projects: [{ repo_root: "/p/a", repo_name: "a", cwd: "/p/a", workspaces: [], error: null }],
  } as unknown as DashboardSnapshotView;

  it("adds a just-registered project as an empty group", () => {
    const next = withProject(base, { repo_root: "/p/b", repo_name: "b", cwd: "/p/b" });
    expect(next?.projects.map((p) => p.cwd)).toEqual(["/p/a", "/p/b"]);
    expect(next?.projects[1]?.workspaces).toEqual([]);
  });

  it("never duplicates a project the snapshot already lists", () => {
    expect(withProject(base, { repo_root: "/p/a", repo_name: "a", cwd: "/p/a" })).toBeNull();
  });
});
