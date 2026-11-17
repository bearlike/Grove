import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

import { runtimeLabel } from "@/components/grove/fleet/tokens";
import {
  profileConflict,
  runtimeKey,
  runtimeKindOf,
  runtimeOptionLabel,
  runtimeRows,
  workspaceRuntimeLabel,
} from "@/lib/grove/adapters/launch-flow";
import type { RuntimeOptionView } from "@/lib/grove/api";
import { agentSummary } from "@/tests/fixtures/agents";

function option(overrides: Partial<RuntimeOptionView> & Pick<RuntimeOptionView, "id" | "kind">): RuntimeOptionView {
  return {
    driver: null,
    driver_label: null,
    node: null,
    workspace_target: true,
    status: "ready",
    reason: null,
    setup: null,
    ...overrides,
  };
}

/** The shape `GET /launch/options` documents, in its documented order. */
const INVENTORY: RuntimeOptionView[] = [
  option({ id: "host", kind: "host" }),
  option({ id: "container", kind: "container", status: "unavailable", reason: "Docker is not running." }),
  option({ id: "lab", kind: "remote", driver: "opensandbox", driver_label: "OpenSandbox", status: "unknown" }),
  option({ id: "nightly", kind: "remote", driver: "cloud_run_job", workspace_target: false, status: "unavailable", reason: "episodic: one task per invocation" }),
  option({
    id: "driver:aca_job",
    kind: "remote",
    driver: "aca_job",
    driver_label: "Azure Container Apps job",
    status: "not_configured",
    setup: "Add a runtimes.<name> block with kind: aca_job.",
  }),
  option({ id: "host", kind: "host", node: "edge-1", status: "unknown" }),
  option({ id: "gpu", kind: "remote", node: "edge-1", status: "unknown" }),
];

const rows = (node: string | null) => runtimeRows(INVENTORY, node, runtimeLabel);

describe("the runtime picker is the engine's inventory", () => {
  it("never offers a task-only block, which cannot be a workspace", () => {
    expect(rows(null).map((row) => row.runtime)).not.toContain("nightly");
  });

  it("lists an implemented driver nobody configured, disabled with its setup step", () => {
    const driver = rows(null).find((row) => row.runtime === "driver:aca_job")!;
    expect(driver.label).toBe("Azure Container Apps job");
    expect(driver.unavailable).toBe("Add a runtimes.<name> block with kind: aca_job.");
  });

  it("disables an unavailable runtime with the engine's reason", () => {
    expect(rows(null).find((row) => row.runtime === "container")!.unavailable).toBe("Docker is not running.");
  });

  it("keeps an unmeasured runtime selectable and says only that nothing checked it", () => {
    // Never "ready": every remote is `unknown`, and the engine probes nothing.
    const lab = rows(null).find((row) => row.runtime === "lab")!;
    expect(lab.unavailable).toBeNull();
    expect(lab.note).toBe("OpenSandbox · readiness not checked");
    expect(rows(null).find((row) => row.runtime === "host")!.note).toBeNull();
  });

  it("lists what can be picked first, then what this host cannot run, then what nobody set up", () => {
    // The inventory interleaves them (host, container, a block, a driver); the
    // picker must not, or an unavailable row sits above a selectable one.
    expect(rows(null).map((row) => [row.runtime, row.group])).toEqual([
      ["host", "available"],
      ["lab", "available"],
      ["container", "unavailable"],
      ["driver:aca_job", "setup"],
    ]);
  });

  it("shows this daemon's rows, or exactly one node's, never both", () => {
    expect(rows(null).every((row) => row.node === null)).toBe(true);
    expect(rows("edge-1").map((row) => row.runtime)).toEqual(["host", "gpu"]);
  });

  it("keys a node's host apart from this daemon's host", () => {
    expect(runtimeKey("host", null)).not.toBe(runtimeKey("host", "edge-1"));
    expect(new Set([...rows(null), ...rows("edge-1")].map((row) => row.key)).size).toBe(
      rows(null).length + rows("edge-1").length,
    );
  });
});

describe("each row is named for what it is", () => {
  it("uses the shared runtime word for host and container", () => {
    expect(runtimeOptionLabel({ id: "container", kind: "container", driver_label: null }, runtimeLabel)).toBe("Dev Container");
  });

  it("names a configured remote by its block, and an unconfigured one by the engine's label", () => {
    expect(runtimeOptionLabel({ id: "lab", kind: "remote", driver_label: "OpenSandbox" }, runtimeLabel)).toBe("lab");
    expect(
      runtimeOptionLabel({ id: "driver:aca_job", kind: "remote", driver_label: "Azure Container Apps job" }, runtimeLabel),
    ).toBe("Azure Container Apps job");
  });

  it("never shows a config keyword, even when the engine sent no label", () => {
    // An older daemon has no `driver_label`: the row says "Remote", never `aca_job`.
    const label = runtimeOptionLabel({ id: "driver:aca_job", kind: "remote", driver_label: null }, runtimeLabel);
    expect(label).toBe("Remote");
    for (const row of rows(null)) expect(row.label, row.runtime).not.toMatch(/_|^driver:/);
  });
});

describe("a draft's runtime choice has one kind", () => {
  it("reads a block name as remote", () => {
    expect(runtimeKindOf("host")).toBe("host");
    expect(runtimeKindOf("container")).toBe("container");
    expect(runtimeKindOf("lab")).toBe("remote");
  });
});

describe("a profile says up front whether it runs on the chosen runtime", () => {
  it("names the conflict from the entry's own runtimes", () => {
    const shell = agentSummary("shell", "generic");
    expect(profileConflict(shell, "remote")).toBe("shell cannot run on a remote runtime");
    expect(profileConflict(shell, "host")).toBeNull();
  });

  it("says nothing while the runtime is unresolved", () => {
    expect(profileConflict(agentSummary("shell", "generic"), null)).toBeNull();
  });
});

describe("an existing workspace shows the runtime it actually has", () => {
  it("names the remote block a remote workspace runs on", () => {
    expect(workspaceRuntimeLabel({ runtime: "remote", remote_runtime: "lab" }, runtimeLabel)).toBe("Remote · lab");
  });

  it("uses the plain word where there is no block", () => {
    expect(workspaceRuntimeLabel({ runtime: "host", remote_runtime: null }, runtimeLabel)).toBe("Host");
    expect(workspaceRuntimeLabel({ runtime: "remote" }, runtimeLabel)).toBe("Remote");
  });
});

describe("no client list of agent kinds decides a launch mode", () => {
  it("offers the session-mode panel from the entry's own launch_modes", () => {
    const agent = readFileSync("components/grove/launch/controls/agent-pill.tsx", "utf8");
    expect(agent).toContain("selectedSpec.launch_modes.length > 1");
    expect(agent).not.toContain("NATIVE_KINDS");
    expect(agent).not.toMatch(/"claude_code",\s*"codex"/);
  });
});
