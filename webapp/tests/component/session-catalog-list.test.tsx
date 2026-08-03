import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SessionCatalogList } from "@/components/sessions/session-catalog-list";
import type { SessionSummaryView } from "@/lib/grove/types";

// Pure presentational — no hooks, no fetch — so these drive the prop contract
// directly. Every fixture is a HOST-scope row (activity/title/prompts null), the
// shape this screen is the only consumer of.

function row(over: Partial<SessionSummaryView> & { session_id: string }): SessionSummaryView {
  return {
    adapter_kind: "claude_code",
    provenance: "fs_discovered",
    primary: false,
    workspace_id: null,
    workspace_title: null,
    workspace_branch: null,
    git_branch: "feat/depth",
    created_at: "2026-07-20T09:00:00Z",
    modified_at: "2026-07-20T11:00:00Z",
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

function rowsOf(): HTMLElement[] {
  return screen.getAllByTestId("session-catalog-row");
}

describe("SessionCatalogList", () => {
  it("groups rows under their project and labels the group", () => {
    render(<SessionCatalogList rows={[row({ session_id: "a" })]} query="" isLoading={false} />);

    const group = screen.getByTestId("session-catalog-group");
    expect(group).toHaveAttribute("data-repo-root", "/repos/grove");
    expect(screen.getByTestId("session-catalog-group-name")).toHaveTextContent("grove");
  });

  it("names the repo-less group honestly instead of hiding those sessions", () => {
    render(
      <SessionCatalogList
        rows={[row({ session_id: "a", cwd: "/tmp/scratch", project: null })]}
        query=""
        isLoading={false}
      />,
    );
    expect(screen.getByTestId("session-catalog-group-name")).toHaveTextContent(
      "No git repository",
    );
    expect(rowsOf()).toHaveLength(1);
  });

  it("links a drillable row to the detail route carrying kind AND the verbatim cwd", () => {
    render(
      <SessionCatalogList
        rows={[row({ session_id: "s-1", adapter_kind: "codex", cwd: "/repos/grove/webapp" })]}
        query=""
        isLoading={false}
      />,
    );

    const link = screen.getByRole("link");
    const href = link.getAttribute("href")!;
    expect(href).toContain("/sessions/s-1");
    expect(href).toContain("kind=codex");
    // The adapters match a RECORDED cwd byte-for-byte, so the path must survive
    // the round-trip encoded, never normalized away.
    expect(decodeURIComponent(href.split("cwd=")[1])).toBe("/repos/grove/webapp");
  });

  it("lists a cwd-less row but renders it inert, with the reason on the row", () => {
    render(
      <SessionCatalogList rows={[row({ session_id: "lost", cwd: null })]} query="" isLoading={false} />,
    );

    const [only] = rowsOf();
    expect(only).toHaveAttribute("data-drillable", "false");
    expect(screen.queryByRole("link")).toBeNull();
    expect(only).toHaveTextContent("location unknown");
    expect(only.getAttribute("title")).toContain("Location unknown");
  });

  it("shows the live cue with a text label, never colour alone", () => {
    render(<SessionCatalogList rows={[row({ session_id: "a", live: true })]} query="" isLoading={false} />);

    expect(rowsOf()[0]).toHaveAttribute("data-live", "true");
    expect(screen.getByTestId("session-catalog-live")).toHaveTextContent("live");
  });

  it("marks Grove-tracked provenance only when a workspace owns the row", () => {
    render(
      <SessionCatalogList
        rows={[row({ session_id: "a", workspace_id: "w1" }), row({ session_id: "b" })]}
        query=""
        isLoading={false}
      />,
    );

    const [managed, plain] = rowsOf();
    expect(managed).toHaveAttribute("data-grove", "true");
    expect(managed).toHaveTextContent("grove");
    expect(plain).toHaveAttribute("data-grove", "false");
  });

  it("labels a row by workspace title, else branch, else id — never 'untitled'", () => {
    render(
      <SessionCatalogList
        rows={[
          row({ session_id: "a", workspace_title: "Wire the panel" }),
          row({ session_id: "b" }),
          row({ session_id: "bare-id", git_branch: null }),
        ]}
        query=""
        isLoading={false}
      />,
    );

    const [titled, branched, bare] = rowsOf();
    expect(titled).toHaveTextContent("Wire the panel");
    expect(branched).toHaveTextContent("feat/depth");
    expect(bare).toHaveTextContent("bare-id");
    expect(screen.queryByText(/untitled/i)).toBeNull();
    // The branch leads the row when it IS the label, so the meta line must not
    // repeat it — one slot, one fact.
    expect(branched.querySelector("[data-testid='session-catalog-branch']")).toBeNull();
    expect(titled.querySelector("[data-testid='session-catalog-branch']")).not.toBeNull();
  });

  it("renders no agent-state mark — the catalog never parsed a transcript", () => {
    const { container } = render(
      <SessionCatalogList rows={[row({ session_id: "a" })]} query="" isLoading={false} />,
    );
    expect(container.querySelector("[data-testid='state-mark']")).toBeNull();
  });

  it("distinguishes a filtered-empty screen from an empty host", () => {
    const { rerender } = render(
      <SessionCatalogList rows={[row({ session_id: "a" })]} query="zzz" isLoading={false} />,
    );
    expect(screen.getByTestId("session-catalog-empty")).toHaveTextContent(
      "No sessions match your search.",
    );

    rerender(<SessionCatalogList rows={[]} query="" isLoading={false} />);
    expect(screen.getByTestId("session-catalog-empty")).toHaveTextContent(
      "No agent sessions found on this host.",
    );
  });
});
