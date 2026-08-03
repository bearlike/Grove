import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TicketLinkage } from "@/components/workspace/ticket-refs";
import type { TicketRef } from "@/lib/grove/types";

// The linked-refs row: issue(s) → PR. Issue-only must stay visible — an
// orchestrator attaches the issue at create and the PR only appears near the
// end, so "no PR yet" is most of a workspace's life and must not read as "no
// linkage". Zero refs still renders nothing at all.

function issue(over: Partial<TicketRef> = {}): TicketRef {
  return {
    provider: "gitea",
    id: "42",
    kind: "issue",
    title: "cards don't show ticket links",
    url: "https://git.example/bearlike/Grove/issues/42",
    status: "open",
    assignee: null,
    ambiguous: false,
    ...over,
  };
}

function pr(over: Partial<TicketRef> = {}): TicketRef {
  return {
    provider: "gitea",
    id: "50",
    kind: "pull_request",
    title: "render ticket links on the card",
    url: "https://git.example/bearlike/Grove/pulls/50",
    status: "open",
    assignee: null,
    ambiguous: false,
    ...over,
  };
}

describe("TicketLinkage", () => {
  it("renders an issue-only workspace: the issue link, no arrow, no PR token", () => {
    render(<TicketLinkage refs={[issue()]} />);
    const row = screen.getByTestId("ticket-linkage");
    const link = screen.getByRole("link", { name: /issue #42/i });
    expect(link).toHaveAttribute("href", "https://git.example/bearlike/Grove/issues/42");
    // The arrow only appears when there is an OUTCOME to point at.
    expect(row.querySelector(".lucide-arrow-right")).toBeNull();
    // The issue keeps the muted treatment — the colored token is the PR's alone.
    expect(row.querySelector(".lucide-git-pull-request")).toBeNull();
  });

  it("renders nothing when there are no ticket refs at all", () => {
    const { container } = render(<TicketLinkage refs={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the PR as a clickable link once one exists", () => {
    render(<TicketLinkage refs={[pr()]} />);
    const row = screen.getByTestId("ticket-linkage");
    expect(row).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /pull request #50, open/i });
    expect(link).toHaveAttribute("href", "https://git.example/bearlike/Grove/pulls/50");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("shows issue(s) → PR when both exist, issues comma-joined", () => {
    render(<TicketLinkage refs={[issue({ id: "42" }), issue({ id: "43" }), pr()]} />);
    const row = screen.getByTestId("ticket-linkage");
    expect(row).toHaveTextContent("#42, #43");
    expect(row.querySelector(".lucide-arrow-right")).not.toBeNull();
    expect(screen.getByRole("link", { name: /issue #42/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /issue #43/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /pull request #50/i })).toBeInTheDocument();
  });

  it("distinguishes PR state by color without a new hue: open/merged/closed", () => {
    const { rerender } = render(<TicketLinkage refs={[pr({ status: "open" })]} />);
    let link = screen.getByRole("link", { name: /pull request #50, open/i });
    expect(link.querySelector("span")).toHaveStyle({ color: "var(--ref-info)" });

    rerender(<TicketLinkage refs={[pr({ status: "MERGED" })]} />);
    link = screen.getByRole("link", { name: /pull request #50, merged/i });
    expect(link.querySelector("span")).toHaveStyle({ color: "var(--phase-done)" });

    rerender(<TicketLinkage refs={[pr({ status: "closed" })]} />);
    link = screen.getByRole("link", { name: /pull request #50, closed/i });
    expect(link.querySelector("span")).toHaveStyle({ color: "var(--status-error)" });
  });

  it("falls back to plain (non-link) text when a ref has no url", () => {
    render(<TicketLinkage refs={[pr({ url: null })]} />);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByTestId("ticket-linkage")).toHaveTextContent("#50");
  });

  it("labels a Linear ticket by its own key, not a bare-number '#' prefix", () => {
    render(<TicketLinkage refs={[issue({ provider: "linear", id: "ENG-123" }), pr()]} />);
    expect(screen.getByTestId("ticket-linkage")).toHaveTextContent("ENG-123");
    expect(screen.getByTestId("ticket-linkage")).not.toHaveTextContent("#ENG-123");
  });
});
