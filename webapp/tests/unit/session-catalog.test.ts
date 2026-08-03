import { describe, it, expect } from "vitest";
import {
  catalogRowLabel,
  groupCatalog,
  isDrillable,
  matchesCatalogQuery,
  relativeCwd,
} from "@/lib/grove/session-catalog";
import type { SessionSummaryView } from "@/lib/grove/types";

/**
 * The catalog's shaping rules, pinned as a contract. Every fixture here is a
 * HOST-scope row: `activity`/`size_bytes`/`title`/the prompts are null, because
 * the host scan pays one bounded head read per session and never parses a
 * transcript. Anything that reads those as "zero" or "untitled" is the bug these
 * tests exist to catch.
 */
function row(over: Partial<SessionSummaryView> & { session_id: string }): SessionSummaryView {
  return {
    adapter_kind: "claude_code",
    provenance: "fs_discovered",
    primary: false,
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: null,
    created_at: null,
    modified_at: null,
    size_bytes: null,
    title: null,
    first_prompt: null,
    last_prompt: null,
    activity: null,
    cwd: "/repos/grove",
    project: {
      repo_root: "/repos/grove",
      repo_name: "grove",
      is_worktree: false,
      is_grove_managed: false,
    },
    live: false,
    ...over,
  };
}

describe("catalogRowLabel", () => {
  it("prefers the workspace title, then the branch, then the bare id", () => {
    expect(catalogRowLabel(row({ session_id: "a", workspace_title: "ship it", git_branch: "b" })))
      .toBe("ship it");
    expect(catalogRowLabel(row({ session_id: "a", git_branch: "feat/x" }))).toBe("feat/x");
    expect(catalogRowLabel(row({ session_id: "abc123" }))).toBe("abc123");
  });
});

describe("isDrillable", () => {
  it("is false without a recorded cwd — the turns route has no coordinate", () => {
    expect(isDrillable(row({ session_id: "a" }))).toBe(true);
    expect(isDrillable(row({ session_id: "a", cwd: null }))).toBe(false);
  });
});

describe("relativeCwd", () => {
  it("returns the subpath under the repo root, null at the root itself", () => {
    expect(relativeCwd(row({ session_id: "a", cwd: "/repos/grove/webapp" }))).toBe("webapp");
    expect(relativeCwd(row({ session_id: "a", cwd: "/repos/grove" }))).toBeNull();
  });

  it("is separator-aware — a sibling prefix is not a subpath", () => {
    expect(relativeCwd(row({ session_id: "a", cwd: "/repos/grove-old" }))).toBeNull();
  });

  it("is null with no project or no cwd", () => {
    expect(relativeCwd(row({ session_id: "a", project: null }))).toBeNull();
    expect(relativeCwd(row({ session_id: "a", cwd: null }))).toBeNull();
  });
});

describe("matchesCatalogQuery", () => {
  const r = row({
    session_id: "abc",
    git_branch: "feat/depth",
    adapter_kind: "codex",
    workspace_title: "Wire the panel",
  });

  it("matches case-insensitively across the fields a catalog row actually has", () => {
    expect(matchesCatalogQuery(r, "")).toBe(true);
    expect(matchesCatalogQuery(r, "DEPTH")).toBe(true);
    expect(matchesCatalogQuery(r, "codex")).toBe(true);
    expect(matchesCatalogQuery(r, "grove")).toBe(true); // repo name + cwd
    expect(matchesCatalogQuery(r, "nothing-here")).toBe(false);
  });
});

describe("groupCatalog", () => {
  it("groups by repo root, newest-first within and across groups", () => {
    const groups = groupCatalog([
      row({ session_id: "old-grove", modified_at: "2026-07-01T00:00:00Z" }),
      row({
        session_id: "new-other",
        modified_at: "2026-07-20T00:00:00Z",
        cwd: "/repos/other",
        project: {
          repo_root: "/repos/other",
          repo_name: "other",
          is_worktree: false,
          is_grove_managed: false,
        },
      }),
      row({ session_id: "new-grove", modified_at: "2026-07-10T00:00:00Z" }),
    ]);

    expect(groups.map((g) => g.repoName)).toEqual(["other", "grove"]);
    expect(groups[1].sessions.map((s) => s.session_id)).toEqual(["new-grove", "old-grove"]);
  });

  it("collects repo-less sessions into one honest group, ordered by recency like any other", () => {
    const groups = groupCatalog([
      row({ session_id: "in-repo", modified_at: "2026-07-01T00:00:00Z" }),
      row({
        session_id: "no-repo",
        modified_at: "2026-07-09T00:00:00Z",
        cwd: "/tmp/scratch",
        project: null,
      }),
    ]);

    expect(groups[0].key).toBe("");
    expect(groups[0].repoName).toBeNull();
    expect(groups[0].sessions.map((s) => s.session_id)).toEqual(["no-repo"]);
    expect(groups[1].repoName).toBe("grove");
  });

  it("marks a group Grove-managed when any of its rows is", () => {
    const groups = groupCatalog([
      row({ session_id: "plain" }),
      row({
        session_id: "managed",
        project: {
          repo_root: "/repos/grove",
          repo_name: "grove",
          is_worktree: true,
          is_grove_managed: true,
        },
      }),
    ]);
    expect(groups[0].isGroveManaged).toBe(true);
  });

  it("keeps undrillable (cwd-less) rows in the listing rather than dropping them", () => {
    const groups = groupCatalog([row({ session_id: "lost", cwd: null, project: null })]);
    expect(groups[0].sessions.map((s) => s.session_id)).toEqual(["lost"]);
  });

  it("applies the query before grouping, so an emptied group disappears entirely", () => {
    const groups = groupCatalog(
      [
        row({ session_id: "a", git_branch: "feat/keep" }),
        row({
          session_id: "b",
          cwd: "/repos/other",
          project: {
            repo_root: "/repos/other",
            repo_name: "other",
            is_worktree: false,
            is_grove_managed: false,
          },
        }),
      ],
      "keep",
    );
    expect(groups).toHaveLength(1);
    expect(groups[0].repoName).toBe("grove");
  });

  it("sinks a null timestamp rather than treating it as 1970", () => {
    const groups = groupCatalog([
      row({ session_id: "unknown-time", modified_at: null }),
      row({ session_id: "dated", modified_at: "2026-01-01T00:00:00Z" }),
    ]);
    expect(groups[0].sessions.map((s) => s.session_id)).toEqual(["dated", "unknown-time"]);
  });
});
