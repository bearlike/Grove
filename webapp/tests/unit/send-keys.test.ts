import { describe, expect, it, vi } from "vitest";

import { GroveClient } from "@/lib/grove/api";
import { canSendKeys } from "@/components/grove/workspace/send-keys";

/**
 * Named-key delivery stays a tiny closed protocol: the browser can name exactly
 * one approved key, and the daemon is the only authority that interprets it.
 */
describe("send keys", () => {
  it("offers the control for both live workspace states", () => {
    expect(canSendKeys("active")).toBe(true);
    expect(canSendKeys("idle")).toBe(true);
    expect(canSendKeys("paused")).toBe(false);
    expect(canSendKeys("offline")).toBe(false);
  });

  it("posts precisely one named key to the workspace key endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await GroveClient.create().sendKey("workspace / one", "C-c");

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/grove/workspaces/workspace%20%2F%20one/keys",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ key: "C-c" }),
      }),
    );
  });
});
