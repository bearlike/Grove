import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { CommitList } from "@/components/workspace/commit-list";
import type { CommitSummaryView } from "@/lib/grove/types";

function commit(over: Partial<CommitSummaryView>): CommitSummaryView {
  return {
    sha: "abc1234",
    subject: "feat: do a thing",
    committed_at: "2026-05-09T10:00:00Z",
    ...over,
  } as CommitSummaryView;
}

describe("CommitList", () => {
  it("renders the loading placeholder while fetching", () => {
    render(<CommitList commits={undefined} isLoading />);
    expect(screen.getByText(/loading commits/i)).toBeInTheDocument();
  });

  it("renders the empty state when commits is []", () => {
    render(<CommitList commits={[]} />);
    expect(screen.getByText(/no commits in this workspace yet/i)).toBeInTheDocument();
  });

  it("shows the commit count and full subject for each row", () => {
    const list = [
      commit({ sha: "aaaaaaa", subject: "feat: scaffold" }),
      commit({ sha: "bbbbbbb", subject: "test: cover the new abstraction" }),
      commit({
        sha: "ccccccc",
        subject: "fix: long subject that should not be truncated visually",
      }),
    ];
    render(<CommitList commits={list} />);
    const list_ = screen.getByTestId("commit-list");
    expect(list_).toBeInTheDocument();
    // count + label live in adjacent spans so match the parent paragraph.
    expect(list_.textContent).toMatch(/3\s*commits since fork/);
    expect(screen.getByText("feat: scaffold")).toBeInTheDocument();
    expect(
      screen.getByText("fix: long subject that should not be truncated visually"),
    ).toBeInTheDocument();
    expect(screen.getByText("aaaaaaa")).toBeInTheDocument();
  });

  it("uses singular noun for one commit", () => {
    render(<CommitList commits={[commit({})]} />);
    const list_ = screen.getByTestId("commit-list");
    expect(list_.textContent).toMatch(/1\s*commit since fork/);
  });
});
