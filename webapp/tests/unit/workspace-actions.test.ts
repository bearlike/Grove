import { describe, it, expect } from "vitest";
import {
  availableActions,
  defaultDeleteBranch,
} from "@/lib/grove/workspace-actions";

// The pure lifecycle-gating policy — a faithful mirror of the engine's TUI
// footer gate (`_AVAILABLE_KEYS_BY_STATUS` + `_KEYS_REMOVED_BY_PLACEMENT`) and
// the manager's `kill(delete_branch=None)` provenance default.

describe("availableActions", () => {
  it("offers pause+kill for a running workspace (active/idle/running)", () => {
    for (const status of ["active", "idle", "running"] as const) {
      expect(availableActions(status, "worktree")).toEqual(["pause", "kill"]);
    }
  });

  it("offers resume+kill when paused", () => {
    expect(availableActions("paused", "worktree")).toEqual(["resume", "kill"]);
  });

  it("offers respawn+kill when offline", () => {
    expect(availableActions("offline", "worktree")).toEqual(["respawn", "kill"]);
  });

  it("offers only kill for orphaned and error", () => {
    expect(availableActions("orphaned", "worktree")).toEqual(["kill"]);
    expect(availableActions("error", "worktree")).toEqual(["kill"]);
  });

  it("offers only kill while provisioning — never respawn, which kills the build", () => {
    // The whole defect: the build window used to read OFFLINE, whose advertised
    // remedy is `respawn` — the one verb that destroys a provision in flight.
    // Kill stays, so a 6-minute image build can still be abandoned.
    expect(availableActions("provisioning", "worktree")).toEqual(["kill"]);
  });

  it("strips pause/resume for root placement (engine refuses them)", () => {
    // A root workspace reconciles to active/idle/offline like any other, but the
    // engine refuses pause/resume — so they must not be offered.
    expect(availableActions("active", "root")).toEqual(["kill"]);
    expect(availableActions("paused", "root")).toEqual(["kill"]);
    expect(availableActions("offline", "root")).toEqual(["respawn", "kill"]);
  });

  it("falls back to kill-only for an unknown status (untrusted wire data)", () => {
    expect(availableActions("totally-new" as never, "worktree")).toEqual(["kill"]);
  });
});

describe("defaultDeleteBranch", () => {
  it("defaults to deleting a grove-created branch", () => {
    expect(defaultDeleteBranch("grove", "worktree")).toBe(true);
  });

  it("keeps a user-attached branch by default", () => {
    expect(defaultDeleteBranch("attached", "worktree")).toBe(false);
  });

  it("never deletes a root workspace's branch, whatever the provenance", () => {
    expect(defaultDeleteBranch("grove", "root")).toBe(false);
    expect(defaultDeleteBranch("attached", "root")).toBe(false);
  });
});
