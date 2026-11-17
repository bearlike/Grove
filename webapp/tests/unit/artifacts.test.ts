import { describe, expect, it } from "vitest";

import {
  artifactDetail,
  artifactFaviconUrl,
  artifactIsDiagram,
  artifactName,
  artifactViewerType,
} from "@/lib/grove/adapters";
import type { ArtifactRef } from "@/lib/grove/api";

const file = (target: string, title: string | null = null): ArtifactRef => ({
  id: "0123456789ab",
  kind: "file",
  target,
  title,
});
const link = (target: string, title: string | null = null): ArtifactRef => ({
  id: "ba9876543210",
  kind: "link",
  target,
  title,
});

describe("artifactName", () => {
  it("prefers the agent's own title", () => {
    expect(artifactName(file("/repo/docs/plan.md", "The plan"))).toBe("The plan");
  });
  it("falls back to the file name, and to the host for a link", () => {
    expect(artifactName(file("/repo/docs/plan.md"))).toBe("plan.md");
    expect(artifactName(link("https://notes.example.com/p/8f3a?v=2"))).toBe("notes.example.com");
  });
});

describe("artifactDetail", () => {
  const roots = ["/repo/.grove/worktrees/w1", "/repo"];
  it("shows a path relative to the most specific root that contains it", () => {
    expect(artifactDetail(file("/repo/.grove/worktrees/w1/docs/plan.md"), roots)).toBe("docs/plan.md");
    expect(artifactDetail(file("/repo/README.md"), roots)).toBe("README.md");
  });
  it("does not treat a sibling directory sharing a prefix as inside the root", () => {
    expect(artifactDetail(file("/repo-other/x.md"), roots)).toBe("/repo-other/x.md");
  });
  it("keeps a file elsewhere and a link as attached", () => {
    expect(artifactDetail(file("/tmp/report.html"), roots)).toBe("/tmp/report.html");
    expect(artifactDetail(link("https://example.com/d"), roots)).toBe("https://example.com/d");
  });
});

describe("artifactFaviconUrl", () => {
  it("asks the linked site itself, keeping a port", () => {
    expect(artifactFaviconUrl(link("https://git.example.com:3000/a/b"))).toBe(
      "https://git.example.com:3000/favicon.ico",
    );
  });
  it("is null for a file and for a target that is not an http(s) URL", () => {
    expect(artifactFaviconUrl(file("/repo/a.md"))).toBeNull();
    expect(artifactFaviconUrl(link("javascript://x"))).toBeNull();
    expect(artifactFaviconUrl(link("not a url"))).toBeNull();
  });
});

describe("artifactIsDiagram", () => {
  it("is true only for a .drawio FILE", () => {
    expect(artifactIsDiagram(file("/repo/Arch.DRAWIO"))).toBe(true);
    expect(artifactIsDiagram(file("/repo/plan.md"))).toBe(false);
    expect(artifactIsDiagram(link("https://example.com/a.drawio"))).toBe(false);
  });
});

describe("artifactViewerType", () => {
  it("lets a name the viewer already renders decide for itself", () => {
    expect(artifactViewerType("plan.md")).toBeUndefined();
    expect(artifactViewerType("prompt.j2.md")).toBeUndefined();
    expect(artifactViewerType("Makefile")).toBeUndefined();
    expect(artifactViewerType("odd.constructor")).toBeUndefined();
  });
  it("renders a diff and JSON Lines as the format that shows them properly", () => {
    expect(artifactViewerType("change.DIFF")).toBe("patch");
    expect(artifactViewerType("events.jsonl")).toBe("json");
    expect(artifactViewerType("events.ndjson")).toBe("json");
  });
  it("reads a Jinja template as the file it produces, or as text", () => {
    expect(artifactViewerType("plan.md.j2")).toBe("md");
    expect(artifactViewerType("nginx.yaml.jinja2")).toBe("yaml");
    expect(artifactViewerType("change.diff.j2")).toBe("patch");
    expect(artifactViewerType("prompt.j2")).toBe("txt");
  });
});
