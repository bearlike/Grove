import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AttachCommand } from "@/components/grove/workspace/controls-tab";

/** The command uses the full stable workspace id even though the CLI accepts a prefix. */
describe("AttachCommand", () => {
  it("renders the paste-ready attach command in the terminal block", () => {
    const workspaceId = "a1b2c3d4-e5f6-7890-a1b2-c3d4e5f67890";
    const html = renderToStaticMarkup(
      <AttachCommand workspaceId={workspaceId} />,
    );

    expect(html).toContain('data-testid="attach-command-card"');
    expect(html).toContain('data-slot="terminal-block"');
    expect(html).toContain(`grove attach ${workspaceId}`);
    expect(html).toContain('aria-label="Copy attach command"');
  });
});
