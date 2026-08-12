import { describe, expect, it } from "vitest";

import { updateTitle } from "@/components/grove/shell/daemon-status";
import type { WhoamiView } from "@/lib/grove/api";

/**
 * The rail footer makes a claim about whether the running daemon is current.
 * There are THREE possible claims and only two of them are ever knowable at
 * once — that is the whole substance of this module, so it is what is pinned.
 */
function identity(patch: Partial<WhoamiView> = {}): WhoamiView {
  return {
    version: "0.4.2",
    started_at: "2026-08-10T09:00:00Z",
    uptime_seconds: 3600,
    host: "example-host",
    user: "example-user",
    platform: "Linux-x86_64",
    python_version: "3.13.0",
    latest_version: null,
    update_available: false,
    ...patch,
  } as WhoamiView;
}

describe("updateTitle", () => {
  it("never claims 'up to date' when the release check has not succeeded", () => {
    // `latest_version: null` means offline / first call / error. "Up to date"
    // would assert something nobody established, and it is the assertion a user
    // relies on to decide NOT to upgrade.
    const title = updateTitle(identity({ latest_version: null, update_available: false }));
    expect(title).toContain("no update information");
    expect(title).not.toContain("up to date");
  });

  it("says up to date only on a check that came back level", () => {
    const title = updateTitle(identity({ latest_version: "0.4.2", update_available: false }));
    expect(title).toContain("up to date");
    expect(title).not.toContain("no update information");
  });

  it("names the version you would be moving to, not just that one exists", () => {
    const title = updateTitle(identity({ latest_version: "0.5.0", update_available: true }));
    expect(title).toContain("0.5.0");
    expect(title).toContain("0.4.2");
    expect(title).not.toContain("up to date");
  });

  it("always names what is actually running", () => {
    for (const patch of [
      { latest_version: null, update_available: false },
      { latest_version: "0.4.2", update_available: false },
      { latest_version: "0.5.0", update_available: true },
    ]) {
      expect(updateTitle(identity(patch))).toContain("0.4.2");
    }
  });
});
