import { describe, expect, it } from "vitest";

import {
  workspaceTitleError,
  workspaceUpdateRequest,
} from "@/components/grove/fleet/rename-workspace-dialog";

describe("workspace metadata edit request", () => {
  it("refuses an empty title with an error beside the field", () => {
    expect(workspaceTitleError("   ")).toBe("A workspace title is required.");
    expect(workspaceUpdateRequest("   ", "A description")).toBeNull();
  });

  it("preserves an empty description as the request to clear it", () => {
    expect(workspaceUpdateRequest("  Rename this  ", "")).toEqual({
      title: "Rename this",
      description: "",
    });
  });
});
