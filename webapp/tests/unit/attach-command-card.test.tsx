import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AttachCommandCard } from "@/components/grove/workspace/controls-tab";

/** The command uses the full stable workspace id even though the CLI accepts a prefix. */
describe("AttachCommandCard", () => {
  it("renders the paste-ready attach command and host/container scope", () => {
    const workspaceId = "a1b2c3d4-e5f6-7890-a1b2-c3d4e5f67890";
    const html = renderToStaticMarkup(
      <AttachCommandCard workspaceId={workspaceId} />,
    );

    expect(html).toContain('data-testid="attach-command-card"');
    expect(html).toContain(`grove attach ${workspaceId}`);
    expect(html).toContain("Run this on the host.");
    expect(html).toContain(
      "Containerized workspaces enter the container’s own tmux session.",
    );
    expect(html).toContain("Copy");
  });
});
