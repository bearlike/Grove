import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LifecycleActions } from "@/components/grove/workspace/lifecycle-actions";
import { availableActions } from "@/components/grove/workspace/selectors";
import { workspace } from "@/tests/fixtures/fleet";

const base = workspace({ id: "native-recovery", branch: "feat/recovery" }).state;

describe("native lifecycle", () => {
  it.each(["root", "worktree"] as const)("offers host recovery for %s placement", (placement) => {
    for (const status of ["running", "active", "idle", "offline"] as const) {
      expect(availableActions({ ...base, placement, status, native: true, runtime: "host" }))
        .toEqual(["respawn", "kill"]);
    }
  });

  it.each(["root", "worktree"] as const)("never offers unsupported native suspension for %s", (placement) => {
    for (const runtime of ["host", "container"] as const) {
      expect(availableActions({ ...base, placement, runtime, native: true, status: "paused" }))
        .toEqual(["kill"]);
    }
  });

  it("does not promise live container recovery through the host restart path", () => {
    for (const status of ["running", "active", "idle"] as const) {
      expect(availableActions({ ...base, native: true, runtime: "container", status }))
        .toEqual(["kill"]);
    }
    expect(availableActions({ ...base, native: true, runtime: "container", status: "offline" }))
      .toEqual(["respawn", "kill"]);
  });

  it.each(["root", "worktree"] as const)("preserves terminal lifecycle for %s", (placement) => {
    for (const status of ["running", "active", "idle"] as const) {
      expect(availableActions({ ...base, placement, native: false, status }))
        .toEqual(placement === "root" ? ["kill"] : ["pause", "kill"]);
    }
    expect(availableActions({ ...base, placement, native: false, status: "paused" }))
      .toEqual(placement === "root" ? ["kill"] : ["resume", "kill"]);
    expect(availableActions({ ...base, placement, native: false, status: "offline" }))
      .toEqual(["respawn", "kill"]);
  });

  it.each(["error", "orphaned", "provisioning"] as const)("does not restart %s workspaces", (status) => {
    expect(availableActions({ ...base, native: true, runtime: "host", status }))
      .toEqual(["kill"]);
  });

  it("renders recovery instead of a pause that the native backend rejects", () => {
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <LifecycleActions
          state={{ ...base, placement: "worktree", native: true, runtime: "host", status: "idle" }}
          onKilled={() => {}}
        />
      </QueryClientProvider>,
    );
    expect(html).toContain('data-testid="lifecycle-respawn"');
    expect(html).not.toContain('data-testid="lifecycle-pause"');
    expect(html).toContain("keeps your files and branch");
    expect(html).toContain("In-flight work is interrupted");
  });
});
