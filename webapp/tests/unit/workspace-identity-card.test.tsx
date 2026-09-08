import { renderToStaticMarkup } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { WorkspaceIdentityCard } from "@/components/grove/workspace/identity";
import { workspace } from "@/tests/fixtures/fleet";

/**
 * The combined card keeps every fact that the former Identity and Branch cards
 * supplied, but gives them one shared field-list anatomy. These render tests pin
 * the reader-facing contract rather than the separate cards it replaced.
 */
function render(card: React.ReactNode): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={new QueryClient()}>{card}</QueryClientProvider>,
  );
}

describe("WorkspaceIdentityCard", () => {
  it("keeps identity and branch facts in one card", () => {
    const state = {
      ...workspace({ id: "identity-card", branch: "feat/identity-card" }).state,
      runtime: "container" as const,
      runtime_default_config: true,
      runtime_fallback_reason: "Image startup failed.",
    };

    const html = render(<WorkspaceIdentityCard state={state} />);

    expect(html).toContain('data-testid="workspace-identity-card"');
    expect(html).toContain("Status");
    expect(html).toContain("Runtime");
    expect(html).toContain("default");
    expect(html).toContain("Fell back to host");
    expect(html).toContain("Agent");
    expect(html).toContain("Branch");
    expect(html).toContain("feat/identity-card");
    expect(html).toContain("Base branch");
    expect(html).toContain("main");
    expect(html).not.toContain("<h3>Branch</h3>");
  });

  it("keeps the root and absent-base facts rather than hiding either", () => {
    const state = {
      ...workspace({ id: "root-identity", branch: "main" }).state,
      placement: "root" as const,
      base_branch: "HEAD",
    };

    const html = render(<WorkspaceIdentityCard state={state} />);

    expect(html).toContain("Placement");
    expect(html).toContain("Root checkout");
    expect(html).toContain("no separate base branch");
    expect(html).not.toContain("HEAD");
  });
});

describe("the session mode", () => {
  it("names the channel Grove's controls take, and both modes are distinct words", () => {
    const base = workspace({ id: "mode", branch: "main" }).state;
    const native = render(
      <WorkspaceIdentityCard state={{ ...base, native: true }} />,
    );
    const terminal = render(
      <WorkspaceIdentityCard state={{ ...base, native: false }} />,
    );
    expect(native).toContain('data-testid="workspace-session-mode"');
    expect(native).toContain("Native session");
    expect(native).not.toContain("Terminal");
    expect(terminal).toContain("Terminal");
    expect(terminal).not.toContain("Native session");
  });
});
